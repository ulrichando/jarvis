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

export type WorkerDeps = {
  store: JobStore
  mintToken: (installationId: number) => Promise<string>
  runInSandbox: (job: Job, token: string) => Promise<{ ok: boolean; error?: string }>
  dailyCap: number
  concurrency: number
  log?: (m: string) => void
}

export type RunOutcome = 'idle' | 'deferred' | 'ran' | 'failed'

export async function runWorkerOnce(deps: WorkerDeps): Promise<RunOutcome> {
  if ((await deps.store.countToday()) >= deps.dailyCap) {
    deps.log?.(`worker: daily cap (${deps.dailyCap}) reached — deferring`)
    return 'deferred'
  }
  const job = await deps.store.claimNext()
  if (!job) return 'idle'
  try {
    const token = await deps.mintToken(job.installationId)
    const r = await deps.runInSandbox(job, token)
    if (r.ok) {
      await deps.store.markDone(job.id)
      deps.log?.(`worker: job ${job.id} (${job.repo}#${job.issueNumber}) done`)
      return 'ran'
    }
    await deps.store.markFailed(job.id, r.error ?? 'sandbox run failed')
    deps.log?.(`worker: job ${job.id} failed: ${r.error ?? 'sandbox run failed'}`)
    return 'failed'
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    await deps.store.markFailed(job.id, msg)
    deps.log?.(`worker: job ${job.id} failed: ${msg}`)
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
