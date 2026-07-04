// src/gh-app/codeSession.ts — Phase C: run a bot job as a watchable /code
// session instead of the throwaway sandbox (GH_APP_USE_CODE_SESSIONS).
//
// Three service calls against the web app over the internal docker network:
//   createCodeSession → POST /api/bridge/v1/gh-app/dispatch
//        (mints the App installation token here — the web holds no App
//        private key — and passes it raw; the route stores it in the
//        session's container meta where the scoped git proxy + host-side PR
//        path pick it up)
//   pollUntilDone     → GET  /api/bridge/v1/sessions/{id}   (the `status`
//        field, ccrSessionStatus: running/idle/requires_action)
//   openSessionPr     → POST /api/bridge/v1/sessions/{id}/pr (the EXISTING
//        session PR route — createContainerPR commits as the bot and stamps
//        the session URL into the PR body + Jarvis-Session trailer)
//
// Auth on every call: `Authorization: Bearer <JARVIS_LOCAL_API_TOKEN>`
// satisfies src/web proxy.ts's network bearer gate (and the PR route's
// v1-permissive worker check); the dispatch route's OWN service token rides
// the dedicated X-GH-App-Token header — the two must never share a header.
//
// Everything is injected (fetch + token minter + clock/sleep) so tests are
// hermetic — no web app, no GitHub, no real waiting.
// Spec: docs/superpowers/specs/2026-07-03-jarvis-gh-app-code-session-design.md
import type { Job } from './jobs.js'

export type CodeSessionConfig = {
  /** Internal web-service base (docker network), e.g. http://web:3000. */
  webUrl: string
  /** JARVIS_LOCAL_API_TOKEN — proxy.ts's network bearer gate. */
  bearerToken: string
  /** GH_APP_BRIDGE_TOKEN — the dispatch route's own service token. */
  serviceToken: string
  /** Browser-facing origin the session links are built on (NOT the internal
   * webUrl — links land in GitHub threads and PR bodies). */
  publicOrigin: string
  /** The App bot's committer identity, e.g. `<app-slug>[bot]`. */
  botLogin: string
  model?: string
}

export type CodeSessionDeps = {
  fetch: typeof fetch
  /** Mint a short-lived installation token scoped to the job's repo — the
   * same minter the worker uses (the gh-app holds the App private key). */
  mintToken: (installationId: number, repo: string) => Promise<string>
  /** Injected for tests; defaults to real timers/clock. */
  sleep?: (ms: number) => Promise<void>
  now?: () => number
  log?: (m: string) => void
}

const authHeaders = (cfg: CodeSessionConfig): Record<string, string> => ({
  Authorization: `Bearer ${cfg.bearerToken}`,
})

/**
 * Mint the job's installation token and dispatch the task as a /code
 * session. Throws on any failure (status-only — never echoes the token);
 * the worker maps a throw to markFailed + best-effort thread feedback.
 */
export async function createCodeSession(
  job: Job,
  cfg: CodeSessionConfig,
  deps: CodeSessionDeps,
): Promise<{ sessionId: string; sessionUrl: string }> {
  const installationToken = await deps.mintToken(job.installationId, job.repo)
  const res = await deps.fetch(`${cfg.webUrl}/api/bridge/v1/gh-app/dispatch`, {
    method: 'POST',
    headers: {
      ...authHeaders(cfg),
      'X-GH-App-Token': cfg.serviceToken,
      'content-type': 'application/json',
    },
    body: JSON.stringify({
      repo: job.repo,
      installationToken,
      botLogin: cfg.botLogin,
      task: job.task,
      publicOrigin: cfg.publicOrigin,
      ...(cfg.model ? { model: cfg.model } : {}),
    }),
  })
  if (!res.ok) throw new Error(`code session dispatch failed: HTTP ${res.status}`)
  const raw = (await res.json()) as { session_id?: unknown; session_url?: unknown }
  if (typeof raw.session_id !== 'string' || !raw.session_id || typeof raw.session_url !== 'string' || !raw.session_url) {
    throw new Error('code session dispatch: response missing session_id/session_url')
  }
  return { sessionId: raw.session_id, sessionUrl: raw.session_url }
}

export type PollOutcome = 'done' | 'requires_action' | 'timeout'

/**
 * Poll the session's `status` until the run finishes. 'done' = `idle`
 * observed AFTER `running` (a just-launched session can read idle before the
 * container worker picks up — never mistake that for completion);
 * 'requires_action' = the run stopped waiting on a human (open the session);
 * 'timeout' = the total-wait cap elapsed. Transient fetch failures are
 * tolerated — the deadline bounds them.
 */
export async function pollUntilDone(
  sessionId: string,
  cfg: CodeSessionConfig,
  deps: CodeSessionDeps,
  opts: { intervalMs?: number; timeoutMs?: number } = {},
): Promise<PollOutcome> {
  const intervalMs = opts.intervalMs ?? 5_000
  const timeoutMs = opts.timeoutMs ?? 900_000 // mirrors the sandbox's 900 s default cap
  const now = deps.now ?? Date.now
  const sleep = deps.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)))
  const deadline = now() + timeoutMs
  let sawRunning = false
  for (;;) {
    try {
      const res = await deps.fetch(`${cfg.webUrl}/api/bridge/v1/sessions/${sessionId}`, {
        headers: authHeaders(cfg),
      })
      if (res.ok) {
        const raw = (await res.json()) as { status?: unknown }
        const s = typeof raw.status === 'string' ? raw.status : ''
        if (s === 'requires_action') return 'requires_action'
        if (s === 'running') sawRunning = true
        else if (s === 'idle' && sawRunning) return 'done'
      }
    } catch (e) {
      deps.log?.(`codeSession: poll ${sessionId} failed (retrying): ${e instanceof Error ? e.message : String(e)}`)
    }
    if (now() >= deadline) return 'timeout'
    await sleep(intervalMs)
  }
}

/**
 * Open the PR through the session's EXISTING PR route (createContainerPR
 * commits as the bot via the session-meta installation token and stamps the
 * session URL into the PR body + `Jarvis-Session:` trailer). Returns the PR
 * (or compare) URL; throws on failure.
 */
export async function openSessionPr(
  sessionId: string,
  cfg: CodeSessionConfig,
  deps: CodeSessionDeps,
): Promise<{ url: string }> {
  const res = await deps.fetch(`${cfg.webUrl}/api/bridge/v1/sessions/${sessionId}/pr`, {
    method: 'POST',
    headers: { ...authHeaders(cfg), 'content-type': 'application/json' },
    body: JSON.stringify({}),
  })
  if (!res.ok) throw new Error(`session PR failed: HTTP ${res.status}`)
  const raw = (await res.json()) as { url?: unknown }
  if (typeof raw.url !== 'string' || !raw.url) throw new Error('session PR: response missing url')
  return { url: raw.url }
}

// --- env wiring (server.ts main block) ---

/** The Phase C rollout flag. DEFAULT OFF — the sandbox path stays
 * byte-identical until GH_APP_USE_CODE_SESSIONS=1|true is set. */
export function useCodeSessions(env: Record<string, string | undefined>): boolean {
  const v = (env.GH_APP_USE_CODE_SESSIONS ?? '').trim().toLowerCase()
  return v === '1' || v === 'true'
}

const stripSlash = (s: string) => s.replace(/\/+$/, '')

export function codeSessionConfigFromEnv(env: Record<string, string | undefined>): CodeSessionConfig {
  return {
    // Compose-stack default: the `web` service on the internal docker network.
    webUrl: stripSlash(env.GH_APP_WEB_URL ?? 'http://web:3000'),
    bearerToken: env.JARVIS_LOCAL_API_TOKEN ?? '',
    serviceToken: env.GH_APP_BRIDGE_TOKEN ?? '',
    publicOrigin: stripSlash(env.GH_APP_PUBLIC_CODE_ORIGIN ?? 'https://0wlan.com'),
    // Default matches the bot identity the thread feedback already presents
    // (**jarvis-gh-bot**); override with the deployed App's real slug.
    botLogin: env.GH_APP_BOT_LOGIN ?? 'jarvis-gh-bot[bot]',
    model: env.GH_APP_SESSION_MODEL || undefined,
  }
}

/** The worker-shaped hooks (mirror of workerFeedback/jobStore bindings). */
export type WorkerCodeSessions = {
  create: (job: Job) => Promise<{ sessionId: string; sessionUrl: string }>
  poll: (sessionId: string) => Promise<PollOutcome>
  openPr: (sessionId: string) => Promise<{ url: string }>
}

export function makeCodeSessions(
  cfg: CodeSessionConfig,
  deps: CodeSessionDeps,
  opts: { intervalMs?: number; timeoutMs?: number } = {},
): WorkerCodeSessions {
  return {
    create: (job) => createCodeSession(job, cfg, deps),
    poll: (sessionId) => pollUntilDone(sessionId, cfg, deps, opts),
    openPr: (sessionId) => openSessionPr(sessionId, cfg, deps),
  }
}
