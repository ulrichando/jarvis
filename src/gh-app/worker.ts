// src/gh-app/worker.ts — claim → mint installation token → sandboxed run.
//
// All effects are injected (store/mintToken/runInSandbox) so the whole
// lifecycle is unit-testable with zero GitHub/Postgres/Docker. Ordering
// rules that matter:
//   - the daily cap is checked BEFORE claiming, so a capped day leaves jobs
//     queued (they run tomorrow) instead of burning them as failed;
//   - any throw after claim (mint or sandbox) lands in markFailed — a claimed
//     job may never be left 'running' forever;
//   - Postgres's SKIP LOCKED claim makes N parallel slots safe.
import type { Job, JobStore } from './jobs.js'
import type { SandboxResult, WorkerFeedback } from './feedback.js'
import type { WorkerCodeSessions } from './codeSession.js'

export type WorkerDeps = {
  store: JobStore
  /** Mint a short-lived installation token scoped to the job's repo. */
  mintToken: (installationId: number, repo: string) => Promise<string>
  runInSandbox: (job: Job, token: string) => Promise<SandboxResult>
  /** Phase C (GH_APP_USE_CODE_SESSIONS): when present, jobs run as watchable
   * /code sessions (dispatch → poll → PR) INSTEAD of runInSandbox. Absent —
   * the default — the sandbox path is byte-identical to before Phase C. */
  codeSessions?: WorkerCodeSessions
  /** Visible thread feedback (👀 + tracking comment + outcome edit).
   * Optional and STRICTLY best-effort — every call is try/caught here; a
   * feedback failure must never fail (or block) the job itself. */
  feedback?: WorkerFeedback
  dailyCap: number
  concurrency: number
  log?: (m: string) => void
}

export type RunOutcome = 'idle' | 'deferred' | 'ran' | 'failed'

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e))

export async function runWorkerOnce(deps: WorkerDeps): Promise<RunOutcome> {
  if ((await deps.store.countToday()) >= deps.dailyCap) {
    deps.log?.(`worker: daily cap (${deps.dailyCap}) reached — deferring`)
    return 'deferred'
  }
  const job = await deps.store.claimNext()
  if (!job) return 'idle'
  let token: string | null = null
  let tracking: number | null = null
  let sessionUrl: string | null = null // set on the code-session path only
  // Edit the tracking comment into the outcome (feedback falls back to a
  // fresh comment when there is none). Requires a token: a mint failure
  // means no acknowledge happened and GitHub would refuse us anyway.
  const tellThread = async (result: SandboxResult) => {
    if (!deps.feedback || token === null) return
    try { await deps.feedback.report(job, tracking, result, token) }
    catch (e) { deps.log?.(`worker: job ${job.id} report failed (ignored): ${errMsg(e)}`) }
  }
  try {
    token = await deps.mintToken(job.installationId, job.repo)
    // I2: PR jobs ALWAYS take the sandbox, even with code sessions on — the
    // session path clones the DEFAULT branch (wrong tree for "fix this PR")
    // and has no analog of the sandbox's untrusted-PR-head refusal (fork /
    // non-allowlisted author, src/cli/src/gh-agent/task.ts). v1 scopes
    // /code sessions to issue/comment jobs only.
    if (deps.codeSessions && !job.isPR) {
      // — Phase C code-session path (GH_APP_USE_CODE_SESSIONS): dispatch the
      // job as a watchable /code session, poll it to completion, open the PR
      // through the session's PR route. Any service-call throw (dispatch /
      // PR) lands in the outer catch → markFailed + best-effort thread
      // feedback, exactly like a sandbox failure.
      const cs = deps.codeSessions
      const s = await cs.create(job)
      sessionUrl = s.sessionUrl
      // Traceability, not control flow — a lost row update must not kill a
      // dispatched run (mirrors setTrackingComment's forgiveness).
      try { await deps.store.setSession(job.id, s.sessionId, s.sessionUrl) }
      catch (e) { deps.log?.(`worker: job ${job.id} setSession failed (ignored): ${errMsg(e)}`) }
      if (deps.feedback) {
        // 👀 + "working on it — watch it live: <session>" AFTER dispatch so
        // the tracking comment can carry the link from the first post.
        try {
          tracking = await deps.feedback.acknowledge(job, token, s.sessionUrl)
          if (tracking !== null) await deps.store.setTrackingComment(job.id, tracking)
        } catch (e) { deps.log?.(`worker: job ${job.id} acknowledge failed (ignored): ${errMsg(e)}`) }
      }
      const outcome = await cs.poll(s.sessionId)
      if (outcome === 'done') {
        const pr = await cs.openPr(s.sessionId)
        await deps.store.markDone(job.id)
        deps.log?.(`worker: job ${job.id} (${job.repo}#${job.issueNumber}) done — ${s.sessionUrl}`)
        await tellThread({ ok: true, prUrl: pr.url, sessionUrl: s.sessionUrl })
        return 'ran'
      }
      const errText = outcome === 'timeout'
        ? 'code session timed out before completing'
        : 'code session requires action — open the session to continue'
      await deps.store.markFailed(job.id, errText)
      deps.log?.(`worker: job ${job.id} failed: ${errText}`)
      await tellThread({ ok: false, error: errText, sessionUrl: s.sessionUrl })
      return 'failed'
    }
    if (deps.feedback) {
      // 👀 + "working on it" BEFORE the sandbox — the user sees the pickup.
      try {
        tracking = await deps.feedback.acknowledge(job, token)
        if (tracking !== null) await deps.store.setTrackingComment(job.id, tracking)
      } catch (e) { deps.log?.(`worker: job ${job.id} acknowledge failed (ignored): ${errMsg(e)}`) }
    }
    const r = await deps.runInSandbox(job, token)
    if (r.ok) {
      await deps.store.markDone(job.id)
      deps.log?.(`worker: job ${job.id} (${job.repo}#${job.issueNumber}) done`)
      await tellThread(r)
      return 'ran'
    }
    await deps.store.markFailed(job.id, r.error ?? 'sandbox run failed')
    deps.log?.(`worker: job ${job.id} failed: ${r.error ?? 'sandbox run failed'}`)
    await tellThread(r)
    return 'failed'
  } catch (e) {
    const msg = errMsg(e)
    await deps.store.markFailed(job.id, msg)
    deps.log?.(`worker: job ${job.id} failed: ${msg}`)
    // A thrown mint/sandbox/session call must still resolve the thread —
    // never leave the tracking comment stuck on "working on it". Keep the
    // session link when the run got far enough to have one.
    await tellThread(sessionUrl ? { ok: false, error: msg, sessionUrl } : { ok: false, error: msg })
    return 'failed'
  }
}

/**
 * Process the queue with at most `deps.concurrency` sandbox runs in flight,
 * until it is empty ('idle') or the daily cap defers. Each slot re-checks the
 * cap before every claim, so the cap holds mid-drain too.
 */
export async function drainQueue(deps: WorkerDeps): Promise<void> {
  const slots = Math.max(1, deps.concurrency)
  const slot = async () => {
    for (;;) {
      const r = await runWorkerOnce(deps)
      if (r === 'idle' || r === 'deferred') return
    }
  }
  await Promise.all(Array.from({ length: slots }, slot))
}

/** Poll loop for the server process: drain, sleep, repeat. */
export function startWorker(deps: WorkerDeps, pollMs = 5000): { stop: () => void } {
  let stopped = false
  ;(async () => {
    while (!stopped) {
      try {
        await drainQueue(deps)
      } catch (e) {
        // drainQueue only throws if the STORE itself fails (per-job errors are
        // caught inside runWorkerOnce) — log and keep polling.
        deps.log?.(`worker: drain error: ${e instanceof Error ? e.message : String(e)}`)
      }
      await new Promise((r) => setTimeout(r, pollMs))
    }
  })()
  return { stop: () => { stopped = true } }
}

export type Caps = { concurrency: number; timeoutSec: number; dailyCap: number }

export function capsFromEnv(env: Record<string, string | undefined>): Caps {
  const posInt = (v: string | undefined, dflt: number) => {
    const n = Number(v)
    return Number.isInteger(n) && n > 0 ? n : dflt
  }
  return {
    concurrency: posInt(env.GH_APP_CONCURRENCY, 2),
    timeoutSec: posInt(env.GH_APP_TIMEOUT_SEC, 900),
    dailyCap: posInt(env.GH_APP_DAILY_CAP, 20),
  }
}
