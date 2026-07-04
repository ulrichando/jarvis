// src/gh-app/codeSession.test.ts
import { test, expect, describe } from 'bun:test'
import {
  createCodeSession, pollUntilDone, openSessionPr,
  useCodeSessions, codeSessionConfigFromEnv, makeCodeSessions,
  type CodeSessionConfig, type CodeSessionDeps,
} from './codeSession.js'
import type { Job } from './jobs.js'

const cfg: CodeSessionConfig = {
  webUrl: 'http://web:3000',
  bearerToken: 'local-api-token',
  serviceToken: 'svc-token',
  publicOrigin: 'https://0wlan.com',
  botLogin: 'talos-hq[bot]',
}

const job: Job = { id: 9, installationId: 555, repo: 'o/r', issueNumber: 7, task: 'fix ci', isPR: false }

function jsonRes(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
}

// Recording fake fetch (mirror of feedback.test.ts): captures (url, init) per
// call; a handler routes canned responses per call index/url — hermetic.
function fakeFetch(handler?: (url: string, init: RequestInit, n: number) => Response | Promise<Response>) {
  const calls: { url: string; init: RequestInit }[] = []
  const f = (async (url: unknown, init?: unknown) => {
    const i = (init ?? {}) as RequestInit
    calls.push({ url: String(url), init: i })
    return handler ? handler(String(url), i, calls.length) : jsonRes(200, {})
  }) as typeof fetch
  return { f, calls }
}

const bodyOf = (c: { init: RequestInit }) => JSON.parse(String(c.init.body)) as any
const headersOf = (c: { init: RequestInit }) => (c.init.headers ?? {}) as Record<string, string>

function mkDeps(f: typeof fetch, over: Partial<CodeSessionDeps> = {}): CodeSessionDeps {
  return { fetch: f, mintToken: async () => 'ghs_inst_tok', sleep: async () => {}, ...over }
}

// I1: a fetch that NEVER resolves on its own — it settles only when the
// caller's AbortSignal fires (rejecting with the signal's TimeoutError). A
// call site that forgot its signal would hang the test past its own timeout.
function hangingFetch() {
  const calls: { url: string; init: RequestInit }[] = []
  const f = ((url: unknown, init?: unknown) => {
    const i = (init ?? {}) as RequestInit
    calls.push({ url: String(url), init: i })
    return new Promise<Response>((_resolve, reject) => {
      const s = i.signal
      if (!s) return // no signal → never settles
      if (s.aborted) return reject(s.reason)
      s.addEventListener('abort', () => reject(s.reason), { once: true })
    })
  }) as typeof fetch
  return { f, calls }
}

describe('gh-app createCodeSession', () => {
  test('mints the installation token for the job repo and dispatches with BOTH auth headers', async () => {
    const { f, calls } = fakeFetch(() => jsonRes(200, { session_id: 'ab12', session_url: 'https://0wlan.com/code/session_ab12' }))
    const minted: { id: number; repo: string }[] = []
    const deps = mkDeps(f, { mintToken: async (id, repo) => { minted.push({ id, repo }); return 'ghs_inst_tok' } })
    const s = await createCodeSession(job, cfg, deps)
    expect(s).toEqual({ sessionId: 'ab12', sessionUrl: 'https://0wlan.com/code/session_ab12' })
    expect(minted).toEqual([{ id: 555, repo: 'o/r' }])
    expect(calls.length).toBe(1)
    expect(calls[0]!.url).toBe('http://web:3000/api/bridge/v1/gh-app/dispatch')
    expect(calls[0]!.init.method).toBe('POST')
    const h = headersOf(calls[0]!)
    // Authorization carries the box bearer (satisfies src/web proxy.ts's
    // network gate); the dispatch route's OWN auth rides X-GH-App-Token.
    expect(h.Authorization).toBe('Bearer local-api-token')
    expect(h['X-GH-App-Token']).toBe('svc-token')
    const body = bodyOf(calls[0]!)
    expect(body).toEqual({
      repo: 'o/r',
      installationToken: 'ghs_inst_tok', // RAW minted token — the route stores it in session meta
      botLogin: 'talos-hq[bot]',
      task: 'fix ci',
      publicOrigin: 'https://0wlan.com',
    })
    expect('model' in body).toBe(false) // absent when unconfigured
  })

  test('model is included when configured', async () => {
    const { f, calls } = fakeFetch(() => jsonRes(200, { session_id: 'x', session_url: 'https://0wlan.com/code/session_x' }))
    await createCodeSession(job, { ...cfg, model: 'claude-opus-4-8' }, mkDeps(f))
    expect(bodyOf(calls[0]!).model).toBe('claude-opus-4-8')
  })

  test('non-OK dispatch throws status-only (never echoes the token)', async () => {
    const { f } = fakeFetch(() => jsonRes(500, { error: 'boom' }))
    let msg = ''
    try { await createCodeSession(job, cfg, mkDeps(f)) } catch (e) { msg = (e as Error).message }
    expect(msg).toContain('HTTP 500')
    expect(msg).not.toContain('ghs_inst_tok')
  })

  test('malformed dispatch response (missing session_id/session_url) throws', async () => {
    const { f } = fakeFetch(() => jsonRes(200, { session_id: 'ab12' }))
    expect(createCodeSession(job, cfg, mkDeps(f))).rejects.toThrow(/session_id\/session_url/)
  })
})

describe('gh-app pollUntilDone', () => {
  // A poll step: plain string = a REPORTED status (the worker has PUT state);
  // object = explicit control of worker_reported (the pre-connect default is
  // `status:'running', worker_reported:false` — ccrSessionStatus's
  // fall-through when no worker_state_json exists yet).
  type Step = string | { status: string; worker_reported?: boolean } | Error | number
  const statuses = (list: Step[]) =>
    fakeFetch((_url, _init, n) => {
      const s = list[Math.min(n, list.length) - 1]!
      if (s instanceof Error) throw s
      if (typeof s === 'number') return jsonRes(s, {})
      if (typeof s === 'string') return jsonRes(200, { id: 's1', status: s, worker_reported: true })
      return jsonRes(200, { id: 's1', status: s.status, worker_reported: s.worker_reported ?? true })
    })

  test('resolves done on running → idle; polls with the bearer', async () => {
    const { f, calls } = statuses(['running', 'running', 'idle'])
    const slept: number[] = []
    const r = await pollUntilDone('s1', cfg, mkDeps(f, { sleep: async (ms) => { slept.push(ms) } }), { intervalMs: 250, timeoutMs: 60_000 })
    expect(r).toBe('done')
    expect(calls.length).toBe(3)
    expect(calls[0]!.url).toBe('http://web:3000/api/bridge/v1/sessions/s1')
    expect(headersOf(calls[0]!).Authorization).toBe('Bearer local-api-token')
    expect(slept).toEqual([250, 250])
  })

  test('C1: the real lifecycle — pre-connect running default → init-idle → the actual run → done; never concludes on the init-idle', async () => {
    // The true status sequence of a fresh session: 'running' is the
    // fall-through DEFAULT while no worker_state_json exists (container
    // still booting — zero work done), then the CLI PUTs worker_status
    // 'idle' at connect BEFORE replaying the seeded task (the init-idle
    // trap), then the genuine 'running' turn, then the genuine done 'idle'.
    const { f, calls } = statuses([
      { status: 'running', worker_reported: false }, // pre-connect default — must NOT arm sawRunning
      { status: 'running', worker_reported: false },
      { status: 'idle' },    // init-idle: worker connected, task not started — must NOT read as done
      { status: 'running' }, // the actual turn — THIS arms sawRunning
      { status: 'idle' },    // truly done
    ])
    const r = await pollUntilDone('s1', cfg, mkDeps(f), { intervalMs: 1, timeoutMs: 60_000 })
    expect(r).toBe('done')
    expect(calls.length).toBe(5) // it polled PAST the init-idle (call 3) — done only after the reported running
  })

  test('C1: default poll interval is 2.5s — tight enough to catch the real running between init-idle and done', async () => {
    const { f } = statuses(['running', 'idle'])
    const slept: number[] = []
    const r = await pollUntilDone('s1', cfg, mkDeps(f, { sleep: async (ms) => { slept.push(ms) } }), { timeoutMs: 60_000 })
    expect(r).toBe('done')
    expect(slept).toEqual([2_500])
  })

  test('ignores a just-launched pre-pickup idle — only idle AFTER a reported running counts', async () => {
    const { f, calls } = statuses(['idle', 'idle', 'running', 'idle'])
    const r = await pollUntilDone('s1', cfg, mkDeps(f), { intervalMs: 1, timeoutMs: 60_000 })
    expect(r).toBe('done')
    expect(calls.length).toBe(4)
  })

  test('requires_action resolves immediately', async () => {
    const { f, calls } = statuses(['running', 'requires_action'])
    expect(await pollUntilDone('s1', cfg, mkDeps(f), { intervalMs: 1, timeoutMs: 60_000 })).toBe('requires_action')
    expect(calls.filter((c) => c.init.method !== 'PATCH').length).toBe(2)
  })

  test('caps total wait — clean timeout outcome, no real sleeping (injected clock)', async () => {
    const { f, calls } = statuses(['running'])
    let t = 0
    const deps = mkDeps(f, { now: () => t, sleep: async (ms) => { t += ms } })
    const r = await pollUntilDone('s1', cfg, deps, { intervalMs: 5_000, timeoutMs: 20_000 })
    expect(r).toBe('timeout')
    expect(calls.filter((c) => c.init.method !== 'PATCH').length).toBe(5) // t=0,5k,10k,15k,20k — bounded, then out
  })

  test('transient fetch failures (throw / non-OK) are tolerated until the deadline', async () => {
    const { f } = statuses([new Error('net down'), 502, 'running', 'idle'])
    expect(await pollUntilDone('s1', cfg, mkDeps(f), { intervalMs: 1, timeoutMs: 60_000 })).toBe('done')
  })

  test('M1: archived is TERMINAL — resolves immediately, never polls on to the timeout', async () => {
    const { f, calls } = statuses(['running', 'archived'])
    const r = await pollUntilDone('s1', cfg, mkDeps(f), { intervalMs: 1, timeoutMs: 60_000 })
    expect(r).toBe('archived')
    expect(calls.length).toBe(2) // stopped on the spot; no archive PATCH either — it already is
  })

  test('M3: timeout best-effort ARCHIVES the abandoned session (stop burning tokens)', async () => {
    const patches: { url: string; init: RequestInit }[] = []
    const { f, calls } = fakeFetch((url, init) => {
      if (init.method === 'PATCH') { patches.push({ url, init }); return jsonRes(200, { id: 's1' }) }
      return jsonRes(200, { id: 's1', status: 'running', worker_reported: true })
    })
    let t = 0
    const deps = mkDeps(f, { now: () => t, sleep: async (ms) => { t += ms } })
    expect(await pollUntilDone('s1', cfg, deps, { intervalMs: 5_000, timeoutMs: 10_000 })).toBe('timeout')
    expect(patches.length).toBe(1)
    expect(patches[0]!.url).toBe('http://web:3000/api/bridge/v1/sessions/s1')
    expect(JSON.parse(String(patches[0]!.init.body))).toEqual({ archived: true })
    expect(headersOf(patches[0]!).Authorization).toBe('Bearer local-api-token')
    expect(calls[calls.length - 1]!.init.method).toBe('PATCH') // fired on the way out
  })

  test('M3: requires_action archives too — the worker will never resume it', async () => {
    const patches: string[] = []
    const { f } = fakeFetch((url, init) => {
      if (init.method === 'PATCH') { patches.push(url); return jsonRes(200, { id: 's1' }) }
      return jsonRes(200, { id: 's1', status: 'requires_action', worker_reported: true })
    })
    expect(await pollUntilDone('s1', cfg, mkDeps(f), { intervalMs: 1, timeoutMs: 60_000 })).toBe('requires_action')
    expect(patches).toEqual(['http://web:3000/api/bridge/v1/sessions/s1'])
  })

  test('M3: archive failure is swallowed+logged — the outcome is unchanged (throw AND non-OK)', async () => {
    const logs: string[] = []
    const throwing = fakeFetch((_url, init) => {
      if (init.method === 'PATCH') throw new Error('web down mid-archive')
      return jsonRes(200, { id: 's1', status: 'requires_action', worker_reported: true })
    })
    expect(await pollUntilDone('s1', cfg, mkDeps(throwing.f, { log: (m) => logs.push(m) }), { intervalMs: 1, timeoutMs: 60_000 })).toBe('requires_action')
    expect(logs.some((m) => m.includes('archive') && m.includes('web down mid-archive'))).toBe(true)

    const nonOk = fakeFetch((_url, init) => {
      if (init.method === 'PATCH') return jsonRes(500, {})
      return jsonRes(200, { id: 's1', status: 'requires_action', worker_reported: true })
    })
    const logs2: string[] = []
    expect(await pollUntilDone('s1', cfg, mkDeps(nonOk.f, { log: (m) => logs2.push(m) }), { intervalMs: 1, timeoutMs: 60_000 })).toBe('requires_action')
    expect(logs2.some((m) => m.includes('archive') && m.includes('500'))).toBe(true)
  })
})

describe('gh-app openSessionPr', () => {
  test('POSTs the session PR route with the bearer and returns the PR url', async () => {
    const { f, calls } = fakeFetch(() => jsonRes(200, { url: 'https://github.com/o/r/pull/12', branch: 'jarvis/x' }))
    const r = await openSessionPr('s1', cfg, mkDeps(f))
    expect(r).toEqual({ url: 'https://github.com/o/r/pull/12' })
    expect(calls[0]!.url).toBe('http://web:3000/api/bridge/v1/sessions/s1/pr')
    expect(calls[0]!.init.method).toBe('POST')
    expect(headersOf(calls[0]!).Authorization).toBe('Bearer local-api-token')
  })

  test('non-OK → throws with the status; missing url → throws', async () => {
    const bad = fakeFetch(() => jsonRes(400, { error: 'no changes' }))
    expect(openSessionPr('s1', cfg, mkDeps(bad.f))).rejects.toThrow(/HTTP 400/)
    const empty = fakeFetch(() => jsonRes(200, { branch: 'x' }))
    expect(openSessionPr('s1', cfg, mkDeps(empty.f))).rejects.toThrow(/missing url/)
  })
})

describe('gh-app codeSession fetch abort-bounds (I1)', () => {
  test('every service call carries an AbortSignal — dispatch, poll GET, PR POST', async () => {
    const dispatch = fakeFetch(() => jsonRes(200, { session_id: 'ab12', session_url: 'https://0wlan.com/code/session_ab12' }))
    await createCodeSession(job, cfg, mkDeps(dispatch.f))
    expect(dispatch.calls[0]!.init.signal).toBeInstanceOf(AbortSignal)

    const poll = fakeFetch(() => jsonRes(200, { id: 's1', status: 'running', worker_reported: true }))
    let t = 0
    await pollUntilDone('s1', cfg, mkDeps(poll.f, { now: () => t, sleep: async (ms) => { t += ms } }), { intervalMs: 5_000, timeoutMs: 5_000 })
    expect(poll.calls[0]!.init.signal).toBeInstanceOf(AbortSignal)

    const pr = fakeFetch(() => jsonRes(200, { url: 'https://github.com/o/r/pull/12' }))
    await openSessionPr('s1', cfg, mkDeps(pr.f))
    expect(pr.calls[0]!.init.signal).toBeInstanceOf(AbortSignal)
  })

  test('a hung dispatch rejects at the abort timeout instead of wedging the worker loop', async () => {
    const { f, calls } = hangingFetch()
    await expect(createCodeSession(job, cfg, mkDeps(f, { fetchTimeoutMs: { dispatch: 20 } }))).rejects.toThrow()
    expect(calls.length).toBe(1)
  })

  test('a hung poll GET is abort-bounded — treated as transient, deadline still wins', async () => {
    const { f, calls } = hangingFetch()
    let t = 0
    const deps = mkDeps(f, { now: () => t, sleep: async (ms) => { t += ms }, fetchTimeoutMs: { poll: 10 } })
    const r = await pollUntilDone('s1', cfg, deps, { intervalMs: 5_000, timeoutMs: 10_000 })
    expect(r).toBe('timeout') // resolved — a hung web:3000 can no longer wedge the loop forever
    expect(calls.length).toBeGreaterThanOrEqual(3)
  })

  test('a hung PR POST rejects at the abort timeout (→ the markFailed+feedback path)', async () => {
    const { f, calls } = hangingFetch()
    await expect(openSessionPr('s1', cfg, mkDeps(f, { fetchTimeoutMs: { pr: 20 } }))).rejects.toThrow()
    expect(calls.length).toBe(1)
  })
})

describe('gh-app code-session env wiring', () => {
  test('useCodeSessions: DEFAULT OFF — only an explicit truthy flag turns it on', () => {
    expect(useCodeSessions({})).toBe(false)
    expect(useCodeSessions({ GH_APP_USE_CODE_SESSIONS: undefined })).toBe(false)
    expect(useCodeSessions({ GH_APP_USE_CODE_SESSIONS: '' })).toBe(false)
    expect(useCodeSessions({ GH_APP_USE_CODE_SESSIONS: '0' })).toBe(false)
    expect(useCodeSessions({ GH_APP_USE_CODE_SESSIONS: 'false' })).toBe(false)
    expect(useCodeSessions({ GH_APP_USE_CODE_SESSIONS: 'no' })).toBe(false)
    expect(useCodeSessions({ GH_APP_USE_CODE_SESSIONS: '1' })).toBe(true)
    expect(useCodeSessions({ GH_APP_USE_CODE_SESSIONS: 'true' })).toBe(true)
  })

  test('codeSessionConfigFromEnv: compose-stack defaults; env overrides win; trailing slashes trimmed', () => {
    expect(codeSessionConfigFromEnv({})).toEqual({
      webUrl: 'http://web:3000',
      bearerToken: '',
      serviceToken: '',
      publicOrigin: 'https://0wlan.com',
      botLogin: 'talos-hq[bot]',
      model: undefined,
    })
    expect(codeSessionConfigFromEnv({
      GH_APP_WEB_URL: 'http://127.0.0.1:3000/',
      JARVIS_LOCAL_API_TOKEN: 'bearer-x',
      GH_APP_BRIDGE_TOKEN: 'svc-y',
      GH_APP_PUBLIC_CODE_ORIGIN: 'https://code.example.com/',
      GH_APP_BOT_LOGIN: 'my-app[bot]',
      GH_APP_SESSION_MODEL: 'claude-opus-4-8',
    })).toEqual({
      webUrl: 'http://127.0.0.1:3000',
      bearerToken: 'bearer-x',
      serviceToken: 'svc-y',
      publicOrigin: 'https://code.example.com',
      botLogin: 'my-app[bot]',
      model: 'claude-opus-4-8',
    })
  })

  test('makeCodeSessions binds create/poll/openPr to one cfg+deps (worker-shaped)', async () => {
    const { f, calls } = fakeFetch((url) => {
      if (url.endsWith('/gh-app/dispatch')) return jsonRes(200, { session_id: 's9', session_url: 'https://0wlan.com/code/session_s9' })
      if (url.endsWith('/sessions/s9/pr')) return jsonRes(200, { url: 'https://github.com/o/r/pull/3' })
      return jsonRes(200, { status: calls.length < 3 ? 'running' : 'idle', worker_reported: true })
    })
    const cs = makeCodeSessions(cfg, mkDeps(f), { intervalMs: 1, timeoutMs: 60_000 })
    const s = await cs.create(job)
    expect(s.sessionId).toBe('s9')
    expect(await cs.poll('s9')).toBe('done')
    expect(await cs.openPr('s9')).toEqual({ url: 'https://github.com/o/r/pull/3' })
  })
})
