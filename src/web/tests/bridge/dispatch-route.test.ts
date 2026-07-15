import { describe, expect, test, beforeEach, vi } from 'vitest'
import { _resetForTests, getStore } from '@/lib/bridge/db'
import { createEnvironment, findSession, listInboundSince } from '@/lib/bridge/store'

// POST /api/bridge/v1/dispatch — the phone-first Dispatch composer. Locks the
// two toggles' server behavior:
//  - allow_all_actions derives permission_mode ('bypassPermissions' | 'default')
//    and seeds it as a set_permission_mode control_request BEFORE the prompt
//    (same shape + ordering as the proven /tasks route — the child replays
//    inbound in sequence order, so the mode applies before work starts).
//  - keep_awake acquires the systemd-inhibit sleep lock on create (mocked here
//    so tests never spawn a real inhibitor on the host).

// No session cookie in tests — pin the auth helper to LOCAL_USER_ID, the same
// owner the environment below is created with (mirrors ccr-v2.test.ts).
vi.mock('@/lib/auth-helpers', () => ({
  getUserId: async () => '00000000-0000-0000-0000-000000000001',
  getUserIdOrSharedLocal: async () => '00000000-0000-0000-0000-000000000001',
}))

const keepAwake = vi.hoisted(() => ({
  acquireKeepAwake: vi.fn(),
  releaseKeepAwake: vi.fn(),
}))
vi.mock('@/lib/dispatch/keep-awake', () => keepAwake)

beforeEach(() => {
  _resetForTests()
  keepAwake.acquireKeepAwake.mockClear()
  keepAwake.releaseKeepAwake.mockClear()
})

function makeEnv(): string {
  const { environment_id } = createEnvironment(getStore(), {
    machine_name: 'kali',
    directory: '/tmp',
    max_sessions: 4,
    worker_type: 'jarvis',
    user_id: '00000000-0000-0000-0000-000000000001',
  })
  return environment_id
}

async function dispatch(body: Record<string, unknown>) {
  const { POST } = await import('@/app/api/bridge/v1/dispatch/route')
  return POST(
    new Request('http://127.0.0.1:3000/api/bridge/v1/dispatch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  )
}

type Inbound = {
  type: string
  request?: { subtype?: string; mode?: string }
  message?: { role: string; content: Array<{ type: string; text?: string }> }
}

function seededInbound(sessionId: string): Inbound[] {
  return listInboundSince(getStore(), sessionId, 0).map(
    (r) => JSON.parse(r.payload_json) as Inbound,
  )
}

describe('POST /api/bridge/v1/dispatch — allow_all_actions → permission mode', () => {
  test('allow_all_actions=true seeds set_permission_mode bypassPermissions BEFORE the prompt', async () => {
    const environment_id = makeEnv()
    const res = await dispatch({
      environment_id,
      prompt: 'run unattended',
      allow_all_actions: true,
    })
    expect(res.status).toBe(200)
    const { session_id } = (await res.json()) as { session_id: string }

    const inbound = seededInbound(session_id)
    const modeIdx = inbound.findIndex(
      (p) => p.type === 'control_request' && p.request?.subtype === 'set_permission_mode',
    )
    const promptIdx = inbound.findIndex((p) => p.type === 'user')
    expect(modeIdx).toBeGreaterThanOrEqual(0)
    expect(inbound[modeIdx].request!.mode).toBe('bypassPermissions')
    // Mode must land before the prompt — the child replays inbound in order.
    expect(promptIdx).toBeGreaterThan(modeIdx)
    expect(inbound[promptIdx].message!.content[0].text).toBe('run unattended')

    // Toggle persisted on the session row too.
    const session = findSession(getStore(), session_id)!
    expect(session.dispatch).toBe(1)
    expect(session.allow_all_actions).toBe(1)
  })

  test('allow_all_actions=false seeds set_permission_mode default (ask/approve)', async () => {
    const environment_id = makeEnv()
    const res = await dispatch({
      environment_id,
      prompt: 'ask before acting',
      allow_all_actions: false,
    })
    expect(res.status).toBe(200)
    const { session_id } = (await res.json()) as { session_id: string }

    const modes = seededInbound(session_id).filter(
      (p) => p.type === 'control_request' && p.request?.subtype === 'set_permission_mode',
    )
    expect(modes).toHaveLength(1)
    expect(modes[0].request!.mode).toBe('default')
    expect(findSession(getStore(), session_id)!.allow_all_actions).toBe(0)
  })

  test('allow_all_actions absent also defaults to default (security default)', async () => {
    const environment_id = makeEnv()
    const res = await dispatch({ environment_id, prompt: 'no toggle sent' })
    const { session_id } = (await res.json()) as { session_id: string }
    const modes = seededInbound(session_id).filter(
      (p) => p.type === 'control_request' && p.request?.subtype === 'set_permission_mode',
    )
    expect(modes).toHaveLength(1)
    expect(modes[0].request!.mode).toBe('default')
  })
})

describe('POST /api/bridge/v1/dispatch — keep_awake → sleep inhibitor', () => {
  test('keep_awake=true acquires the keep-awake lock for the new session', async () => {
    const environment_id = makeEnv()
    const res = await dispatch({ environment_id, prompt: 'long task', keep_awake: true })
    expect(res.status).toBe(200)
    const { session_id } = (await res.json()) as { session_id: string }
    expect(keepAwake.acquireKeepAwake).toHaveBeenCalledTimes(1)
    expect(keepAwake.acquireKeepAwake).toHaveBeenCalledWith(session_id)
    expect(findSession(getStore(), session_id)!.keep_awake).toBe(1)
  })

  test('keep_awake=false does not touch the inhibitor', async () => {
    const environment_id = makeEnv()
    const res = await dispatch({ environment_id, prompt: 'short task', keep_awake: false })
    expect(res.status).toBe(200)
    expect(keepAwake.acquireKeepAwake).not.toHaveBeenCalled()
  })

  test('keep_awake absent also does not touch the inhibitor', async () => {
    const environment_id = makeEnv()
    const res = await dispatch({ environment_id, prompt: 'no toggle sent' })
    expect(res.status).toBe(200)
    expect(keepAwake.acquireKeepAwake).not.toHaveBeenCalled()
  })
})
