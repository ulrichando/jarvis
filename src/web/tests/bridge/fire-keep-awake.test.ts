import { describe, expect, test, beforeEach, vi } from 'vitest'
import { _resetForTests, getStore } from '@/lib/bridge/db'
import {
  createEnvironment,
  getOrCreateSession,
  setSessionDispatch,
} from '@/lib/bridge/store'

// Gap-1 coverage: keep-awake must span EVERY active turn of a Dispatch
// session, not just the first. fire.ts releases the lock on each 'idle'
// (task done) — so the 'running' transition (follow-up prompt re-runs the
// worker) must RE-acquire it. Keep-awake is mocked; no real inhibitor spawns.

const keepAwake = vi.hoisted(() => ({
  acquireKeepAwake: vi.fn(),
  releaseKeepAwake: vi.fn(),
}))
vi.mock('@/lib/dispatch/keep-awake', () => keepAwake)

// Never attempt a real Web Push send from tests.
vi.mock('@/lib/push/web-push', () => ({
  sendPushToUser: vi.fn(async () => {}),
}))

import { firePushForStatusTransition } from '@/lib/push/fire'

beforeEach(() => {
  _resetForTests()
  keepAwake.acquireKeepAwake.mockClear()
  keepAwake.releaseKeepAwake.mockClear()
})

function makeSession(flags: { dispatch: boolean; keep_awake: boolean }): string {
  const store = getStore()
  const { environment_id } = createEnvironment(store, {
    machine_name: 'kali',
    directory: '/tmp',
    max_sessions: 4,
    worker_type: 'jarvis',
    user_id: '00000000-0000-0000-0000-000000000001',
  })
  const sessionId = `fire-ka-${Math.random().toString(16).slice(2, 10)}`
  getOrCreateSession(store, sessionId, environment_id)
  setSessionDispatch(store, sessionId, flags)
  return sessionId
}

describe('firePushForStatusTransition — keep-awake across turns', () => {
  test("'running' re-acquires the lock for a dispatch session with keep_awake on", () => {
    const sessionId = makeSession({ dispatch: true, keep_awake: true })
    firePushForStatusTransition(getStore(), sessionId, 'running')
    expect(keepAwake.acquireKeepAwake).toHaveBeenCalledTimes(1)
    expect(keepAwake.acquireKeepAwake).toHaveBeenCalledWith(sessionId)
  })

  test("'running' does NOT acquire when keep_awake is off", () => {
    const sessionId = makeSession({ dispatch: true, keep_awake: false })
    firePushForStatusTransition(getStore(), sessionId, 'running')
    expect(keepAwake.acquireKeepAwake).not.toHaveBeenCalled()
  })

  test("'running' does NOT acquire for a non-dispatch session", () => {
    const sessionId = makeSession({ dispatch: false, keep_awake: true })
    firePushForStatusTransition(getStore(), sessionId, 'running')
    expect(keepAwake.acquireKeepAwake).not.toHaveBeenCalled()
  })

  test("'idle' still releases the lock (task done)", () => {
    const sessionId = makeSession({ dispatch: true, keep_awake: true })
    // fire() releases synchronously before its first await, so the assertion
    // is safe without flushing the fire-and-forget promise.
    firePushForStatusTransition(getStore(), sessionId, 'idle')
    expect(keepAwake.releaseKeepAwake).toHaveBeenCalledWith(sessionId)
  })
})
