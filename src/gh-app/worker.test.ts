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
  let today = startedToday
  const store: JobStore = {
    enqueue: async (j) => { queued.push({ ...j, id: nextId++ }); return nextId - 1 },
    claimNext: async () => { const j = queued.shift() ?? null; if (j) today++; return j },
    markDone: async (id) => { done.push(id) },
    markFailed: async (id, error) => { failed.push({ id, error }) },
    countToday: async () => today,
  }
  return { store, queued, done, failed }
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
