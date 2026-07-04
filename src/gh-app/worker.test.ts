// src/gh-app/worker.test.ts
import { test, expect, describe } from 'bun:test'
import { runWorkerOnce, drainQueue, capsFromEnv, type WorkerDeps } from './worker.js'
import type { Job, NewJob, JobStore } from './jobs.js'

// In-memory JobStore mirroring the Postgres semantics: claiming counts toward
// countToday (started_at is set on claim), queued jobs don't.
function memStore(initial: NewJob[], startedToday = 0) {
  let nextId = 1
  const queued: Job[] = initial.map((j) => ({ ...j, id: nextId++ }))
  const done: number[] = []
  const failed: { id: number; error: string }[] = []
  const tracked: { id: number; trackingCommentId: number }[] = []
  const sessions: { id: number; sessionId: string; sessionUrl: string }[] = []
  let today = startedToday
  const store: JobStore = {
    enqueue: async (j) => { queued.push({ ...j, id: nextId++ }); return nextId - 1 },
    claimNext: async () => { const j = queued.shift() ?? null; if (j) today++; return j },
    markDone: async (id) => { done.push(id) },
    markFailed: async (id, error) => { failed.push({ id, error }) },
    setTrackingComment: async (id, trackingCommentId) => { tracked.push({ id, trackingCommentId }) },
    setSession: async (id, sessionId, sessionUrl) => { sessions.push({ id, sessionId, sessionUrl }) },
    countToday: async () => today,
  }
  return { store, queued, done, failed, tracked, sessions }
}

const job = (n: number): NewJob => ({ installationId: 555, repo: 'o/r', issueNumber: n, task: `t${n}`, isPR: false })

function deps(store: JobStore, over: Partial<WorkerDeps> = {}): WorkerDeps {
  return {
    store,
    mintToken: async () => 'ghs_tok',
    runInSandbox: async () => ({ ok: true }),
    dailyCap: 20,
    concurrency: 2,
    ...over,
  }
}

describe('gh-app worker', () => {
  test('queued job under the cap → mints token scoped to the job repo, runs sandbox, markDone', async () => {
    const { store, done, failed } = memStore([job(1)])
    const minted: { id: number; repo: string }[] = []
    const ran: { job: Job; token: string }[] = []
    const d = deps(store, {
      mintToken: async (id, repo) => { minted.push({ id, repo }); return 'ghs_tok' },
      runInSandbox: async (j, t) => { ran.push({ job: j, token: t }); return { ok: true } },
    })
    const r = await runWorkerOnce(d)
    expect(r).toBe('ran')
    expect(minted).toEqual([{ id: 555, repo: 'o/r' }]) // repo threaded → per-repo token scope
    expect(ran.length).toBe(1)
    expect(ran[0]!.token).toBe('ghs_tok')
    expect(ran[0]!.job.issueNumber).toBe(1)
    expect(done).toEqual([1])
    expect(failed.length).toBe(0)
  })

  test('daily cap reached → deferred, job stays queued, sandbox NOT called', async () => {
    const { store, queued, done, failed } = memStore([job(1)], 20)
    let sandboxCalls = 0
    const d = deps(store, { runInSandbox: async () => { sandboxCalls++; return { ok: true } }, dailyCap: 20 })
    const r = await runWorkerOnce(d)
    expect(r).toBe('deferred')
    expect(sandboxCalls).toBe(0)
    expect(queued.length).toBe(1) // NOT claimed — still there for tomorrow
    expect(done.length).toBe(0)
    expect(failed.length).toBe(0)
  })

  test('empty queue → idle', async () => {
    const { store } = memStore([])
    expect(await runWorkerOnce(deps(store))).toBe('idle')
  })

  test('runInSandbox throws → markFailed with the error recorded', async () => {
    const { store, done, failed } = memStore([job(1)])
    const d = deps(store, { runInSandbox: async () => { throw new Error('container timeout after 900s') } })
    const r = await runWorkerOnce(d)
    expect(r).toBe('failed')
    expect(done.length).toBe(0)
    expect(failed.length).toBe(1)
    expect(failed[0]!.id).toBe(1)
    expect(failed[0]!.error).toContain('container timeout')
  })

  test('runInSandbox resolving ok:false → markFailed too', async () => {
    const { store, failed } = memStore([job(1)])
    const d = deps(store, { runInSandbox: async () => ({ ok: false, error: 'jarvis -p exited 1' }) })
    expect(await runWorkerOnce(d)).toBe('failed')
    expect(failed[0]!.error).toContain('jarvis -p exited 1')
  })

  test('mintToken failure → markFailed, sandbox never invoked', async () => {
    const { store, failed } = memStore([job(1)])
    let sandboxCalls = 0
    const d = deps(store, {
      mintToken: async () => { throw new Error('mint failed: HTTP 401') },
      runInSandbox: async () => { sandboxCalls++; return { ok: true } },
    })
    expect(await runWorkerOnce(d)).toBe('failed')
    expect(sandboxCalls).toBe(0)
    expect(failed[0]!.error).toContain('HTTP 401')
  })

  test('drainQueue: concurrency never exceeds the configured cap', async () => {
    const { store, done } = memStore([job(1), job(2), job(3), job(4), job(5)])
    let inflight = 0
    let maxInflight = 0
    const d = deps(store, {
      concurrency: 2,
      runInSandbox: async () => {
        inflight++
        maxInflight = Math.max(maxInflight, inflight)
        await new Promise((r) => setTimeout(r, 10))
        inflight--
        return { ok: true }
      },
    })
    await drainQueue(d)
    expect(done.length).toBe(5)
    expect(maxInflight).toBeLessThanOrEqual(2)
    expect(maxInflight).toBeGreaterThan(1) // it actually ran in parallel
  })

  test('drainQueue stops at the daily cap mid-drain', async () => {
    const { store, queued, done } = memStore([job(1), job(2), job(3)], 18)
    const d = deps(store, { dailyCap: 20, concurrency: 1 })
    await drainQueue(d)
    expect(done.length).toBe(2)      // 18 + 2 = 20 → cap
    expect(queued.length).toBe(1)    // third stays queued
  })
})

describe('gh-app worker thread feedback', () => {
  type Reported = { jobId: number; tracking: number | null; result: { ok: boolean; error?: string }; token: string }
  function fbRecorder(ackReturn: number | null = 77) {
    const order: string[] = []
    const acked: { jobId: number; token: string }[] = []
    const reported: Reported[] = []
    const feedback = {
      acknowledge: async (j: Job, token: string) => { order.push('ack'); acked.push({ jobId: j.id, token }); return ackReturn },
      report: async (j: Job, tracking: number | null, result: { ok: boolean; error?: string }, token: string) => {
        order.push('report'); reported.push({ jobId: j.id, tracking, result, token })
      },
    }
    return { feedback, order, acked, reported }
  }

  test('acknowledge runs BEFORE the sandbox, report AFTER with the result; tracking id persisted', async () => {
    const { store, done, tracked } = memStore([job(1)])
    const { feedback, order, acked, reported } = fbRecorder(77)
    const d = deps(store, {
      feedback,
      runInSandbox: async () => { order.push('sandbox'); return { ok: true, prUrl: 'https://github.com/o/r/pull/9' } },
    })
    expect(await runWorkerOnce(d)).toBe('ran')
    expect(order).toEqual(['ack', 'sandbox', 'report'])
    expect(acked).toEqual([{ jobId: 1, token: 'ghs_tok' }])
    expect(tracked).toEqual([{ id: 1, trackingCommentId: 77 }])
    expect(reported.length).toBe(1)
    expect(reported[0]!.tracking).toBe(77)
    expect(reported[0]!.result).toEqual({ ok: true, prUrl: 'https://github.com/o/r/pull/9' } as any)
    expect(reported[0]!.token).toBe('ghs_tok')
    expect(done).toEqual([1])
  })

  test('sandbox ok:false → report carries the failed result', async () => {
    const { store, failed } = memStore([job(1)])
    const { feedback, reported } = fbRecorder()
    const d = deps(store, { feedback, runInSandbox: async () => ({ ok: false, error: 'jarvis -p exited 1' }) })
    expect(await runWorkerOnce(d)).toBe('failed')
    expect(failed[0]!.error).toContain('jarvis -p exited 1')
    expect(reported[0]!.result).toEqual({ ok: false, error: 'jarvis -p exited 1' })
  })

  test('sandbox THROW → still reported as a failed outcome (thread never left on "working")', async () => {
    const { store, failed } = memStore([job(1)])
    const { feedback, reported } = fbRecorder()
    const d = deps(store, { feedback, runInSandbox: async () => { throw new Error('container timed out') } })
    expect(await runWorkerOnce(d)).toBe('failed')
    expect(failed[0]!.error).toContain('container timed out')
    expect(reported.length).toBe(1)
    expect(reported[0]!.result.ok).toBe(false)
    expect(reported[0]!.result.error).toContain('container timed out')
  })

  test('acknowledge throwing does NOT fail the job; report still runs with tracking null', async () => {
    const { store, done, tracked } = memStore([job(1)])
    const reported: Reported[] = []
    const d = deps(store, {
      feedback: {
        acknowledge: async () => { throw new Error('github 500') },
        report: async (j: Job, tracking: number | null, result: any, token: string) => { reported.push({ jobId: j.id, tracking, result, token }) },
      },
    })
    expect(await runWorkerOnce(d)).toBe('ran')
    expect(done).toEqual([1])
    expect(tracked.length).toBe(0)
    expect(reported[0]!.tracking).toBeNull()
  })

  test('report throwing does NOT change the job outcome', async () => {
    const { store, done } = memStore([job(1)])
    const { feedback } = fbRecorder()
    const d = deps(store, { feedback: { ...feedback, report: async () => { throw new Error('github down') } } })
    expect(await runWorkerOnce(d)).toBe('ran')
    expect(done).toEqual([1])
  })

  test('acknowledge returning null (post failed) → nothing persisted, report gets null', async () => {
    const { store, tracked } = memStore([job(1)])
    const { feedback, reported } = fbRecorder(null)
    expect(await runWorkerOnce(deps(store, { feedback }))).toBe('ran')
    expect(tracked.length).toBe(0)
    expect(reported[0]!.tracking).toBeNull()
  })

  test('mintToken failure → no token, so neither acknowledge nor report fires', async () => {
    const { store, failed } = memStore([job(1)])
    const { feedback, order } = fbRecorder()
    const d = deps(store, { feedback, mintToken: async () => { throw new Error('mint failed: HTTP 401') } })
    expect(await runWorkerOnce(d)).toBe('failed')
    expect(failed[0]!.error).toContain('HTTP 401')
    expect(order).toEqual([])
  })
})

describe('gh-app worker code-session path (GH_APP_USE_CODE_SESSIONS)', () => {
  const SURL = 'https://0wlan.com/code/session_cs1'
  type CS = NonNullable<WorkerDeps['codeSessions']>

  function csRecorder(over: Partial<CS> = {}) {
    const order: string[] = []
    const cs: CS = {
      create: async () => { order.push('create'); return { sessionId: 'cs1', sessionUrl: SURL } },
      poll: async () => { order.push('poll'); return 'done' },
      openPr: async () => { order.push('pr'); return { url: 'https://github.com/o/r/pull/12' } },
      ...over,
    }
    return { cs, order }
  }

  function fbRecorder() {
    const order: string[] = []
    const acked: { jobId: number; token: string; sessionUrl?: string }[] = []
    const reported: { tracking: number | null; result: { ok: boolean; prUrl?: string; error?: string; sessionUrl?: string } }[] = []
    const feedback = {
      acknowledge: async (j: Job, token: string, sessionUrl?: string) => { order.push('ack'); acked.push({ jobId: j.id, token, sessionUrl }); return 77 },
      report: async (_j: Job, tracking: number | null, result: any) => { order.push('report'); reported.push({ tracking, result }) },
    }
    return { feedback, order, acked, reported }
  }

  test('happy path: create → persist session → ack(with url) → poll → PR → done + report', async () => {
    const { store, done, sessions, tracked } = memStore([job(1)])
    const { cs, order: csOrder } = csRecorder()
    const { feedback, order: fbOrder, acked, reported } = fbRecorder()
    let sandboxCalls = 0
    const d = deps(store, { codeSessions: cs, feedback, runInSandbox: async () => { sandboxCalls++; return { ok: true } } })
    expect(await runWorkerOnce(d)).toBe('ran')
    expect(sandboxCalls).toBe(0) // flag ON → the sandbox path is never taken
    expect(csOrder).toEqual(['create', 'poll', 'pr'])
    expect(fbOrder).toEqual(['ack', 'report'])
    expect(sessions).toEqual([{ id: 1, sessionId: 'cs1', sessionUrl: SURL }]) // persisted on the job row
    expect(tracked).toEqual([{ id: 1, trackingCommentId: 77 }])
    expect(acked).toEqual([{ jobId: 1, token: 'ghs_tok', sessionUrl: SURL }]) // tracking comment can link the live run
    expect(done).toEqual([1])
    expect(reported[0]!.result).toEqual({ ok: true, prUrl: 'https://github.com/o/r/pull/12', sessionUrl: SURL })
  })

  test('dispatch failure → job failed, best-effort report still runs, PR never attempted', async () => {
    const { store, done, failed, sessions } = memStore([job(1)])
    const { cs, order } = csRecorder({ create: async () => { throw new Error('code session dispatch failed: HTTP 401') } })
    const { feedback, reported } = fbRecorder()
    expect(await runWorkerOnce(deps(store, { codeSessions: cs, feedback }))).toBe('failed')
    expect(done.length).toBe(0)
    expect(failed[0]!.error).toContain('HTTP 401')
    expect(sessions.length).toBe(0)
    expect(order).toEqual([]) // neither poll nor pr fired
    expect(reported.length).toBe(1) // thread still resolved
    expect(reported[0]!.result.ok).toBe(false)
    expect(reported[0]!.result.error).toContain('HTTP 401')
  })

  test('poll timeout → job failed with a clear timeout error, report carries the session link', async () => {
    const { store, failed } = memStore([job(1)])
    const { cs, order } = csRecorder({ poll: async () => 'timeout' })
    const { feedback, reported } = fbRecorder()
    expect(await runWorkerOnce(deps(store, { codeSessions: cs, feedback }))).toBe('failed')
    expect(failed[0]!.error).toContain('timed out')
    expect(order).toEqual(['create']) // poll was overridden (not recorded); the point: NO 'pr' on timeout
    expect(order).not.toContain('pr')
    expect(reported[0]!.result).toEqual({ ok: false, error: failed[0]!.error, sessionUrl: SURL } as any)
  })

  test('poll requires_action → job failed pointing at the session to continue', async () => {
    const { store, failed } = memStore([job(1)])
    const { cs } = csRecorder({ poll: async () => 'requires_action' })
    const { feedback, reported } = fbRecorder()
    expect(await runWorkerOnce(deps(store, { codeSessions: cs, feedback }))).toBe('failed')
    expect(failed[0]!.error).toContain('requires action')
    expect(reported[0]!.result.sessionUrl).toBe(SURL)
  })

  test('openSessionPr throwing → job failed, report still resolves the thread with the link', async () => {
    const { store, done, failed } = memStore([job(1)])
    const { cs } = csRecorder({ openPr: async () => { throw new Error('session PR failed: HTTP 400') } })
    const { feedback, reported } = fbRecorder()
    expect(await runWorkerOnce(deps(store, { codeSessions: cs, feedback }))).toBe('failed')
    expect(done.length).toBe(0)
    expect(failed[0]!.error).toContain('HTTP 400')
    expect(reported[0]!.result.ok).toBe(false)
    expect(reported[0]!.result.sessionUrl).toBe(SURL)
  })

  test('setSession persistence failure is swallowed — the run itself proceeds', async () => {
    const { store, done } = memStore([job(1)])
    store.setSession = async () => { throw new Error('pg down') }
    const { cs, order } = csRecorder()
    expect(await runWorkerOnce(deps(store, { codeSessions: cs }))).toBe('ran')
    expect(done).toEqual([1])
    expect(order).toEqual(['create', 'poll', 'pr'])
  })

  test('FLAG OFF (no codeSessions dep) → sandbox path byte-identical: runs sandbox, never dispatches/persists a session', async () => {
    const { store, done, sessions } = memStore([job(1), { ...job(2), isPR: true }])
    let sandboxCalls = 0
    const d = deps(store, { runInSandbox: async () => { sandboxCalls++; return { ok: true } } })
    expect(d.codeSessions).toBeUndefined() // default deps carry NO session runner
    expect(await runWorkerOnce(d)).toBe('ran')
    expect(await runWorkerOnce(d)).toBe('ran') // the isPR job — same sandbox path
    expect(sandboxCalls).toBe(2)
    expect(sessions.length).toBe(0)
    expect(done).toEqual([1, 2])
  })

  test('I2: isPR job with the flag ON still runs the SANDBOX — sessions are issue/comment-only in v1', async () => {
    // The session path clones the DEFAULT branch (wrong tree for a PR job)
    // and has no analog of the sandbox's untrusted-PR-head refusal
    // (fork / non-allowlisted author) — so v1 routes PR jobs to the sandbox
    // even when GH_APP_USE_CODE_SESSIONS is on.
    const { store, done, failed, sessions } = memStore([{ ...job(1), isPR: true }])
    const { cs, order } = csRecorder()
    const { feedback, acked, reported } = fbRecorder()
    let sandboxCalls = 0
    const d = deps(store, { codeSessions: cs, feedback, runInSandbox: async () => { sandboxCalls++; return { ok: true, prUrl: 'https://github.com/o/r/pull/1' } } })
    expect(await runWorkerOnce(d)).toBe('ran')
    expect(sandboxCalls).toBe(1)
    expect(order).toEqual([]) // never dispatched/polled/PR'd a session
    expect(sessions.length).toBe(0)
    expect(acked[0]!.sessionUrl).toBeUndefined() // sandbox-shaped ack — no live-session link
    expect(reported[0]!.result.ok).toBe(true)
    expect(done).toEqual([1])
    expect(failed.length).toBe(0)
  })
})

describe('gh-app capsFromEnv', () => {
  test('defaults: concurrency 2, timeout 900, daily cap 20', () => {
    expect(capsFromEnv({})).toEqual({ concurrency: 2, timeoutSec: 900, dailyCap: 20 })
  })
  test('env overrides win; garbage/non-positive falls back', () => {
    expect(capsFromEnv({ GH_APP_CONCURRENCY: '4', GH_APP_TIMEOUT_SEC: '300', GH_APP_DAILY_CAP: '5' }))
      .toEqual({ concurrency: 4, timeoutSec: 300, dailyCap: 5 })
    expect(capsFromEnv({ GH_APP_CONCURRENCY: '0', GH_APP_TIMEOUT_SEC: '-1', GH_APP_DAILY_CAP: 'lots' }))
      .toEqual({ concurrency: 2, timeoutSec: 900, dailyCap: 20 })
  })
})
