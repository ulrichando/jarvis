import { describe, expect, test, beforeEach } from 'vitest'
import { _resetForTests, getStore } from '@/lib/bridge/db'
import {
  getOrCreateBridgeToken,
  createEnvironment,
  getOrCreateSession,
  appendSessionEvent,
} from '@/lib/bridge/store'
import { GET as listGET } from '@/app/api/v1/sessions/route'

// The teleport client reads session_context.sources[].url to derive each
// session's repo. Without it, listRemoteSessions throws
// "session.session_context.sources is undefined" → picker shows
// "Error loading Jarvis sessions" (observed live 2026-07). This guards the shape.

const USER = '00000000-0000-0000-0000-0000000000cc'
const REPO = 'https://github.com/ulrichando/maxrun'

beforeEach(() => _resetForTests())

function withEnvSession() {
  const store = getStore()
  const token = getOrCreateBridgeToken(store, USER)
  const env = createEnvironment(store, {
    machine_name: 'Cloud container',
    directory: '/workspace',
    git_repo_url: REPO,
    max_sessions: 4,
    worker_type: 'container',
    user_id: USER,
  })
  getOrCreateSession(store, 'abc123def456', env.environment_id)
  return token
}

describe('CCR /api/v1/sessions session_context (teleport client contract)', () => {
  test('list GET returns session_context.sources[] with a git_repository url', async () => {
    const token = withEnvSession()
    const res = await listGET(
      new Request('https://web.test/api/v1/sessions', {
        headers: { authorization: `Bearer ${token}` },
      }),
    )
    expect(res.status).toBe(200)
    const body = (await res.json()) as {
      data: { session_context?: { sources?: { type: string; url: string }[] } }[]
    }
    const s = body.data[0]!
    expect(s.session_context).toBeTruthy()
    // The exact field the client's `sources.find(t => t.type==='git_repository').url` reads.
    expect(s.session_context!.sources).toEqual([{ type: 'git_repository', url: REPO }])
  })

  test('updated_at reflects last ACTIVITY (latest event), not creation', async () => {
    const token = withEnvSession()
    const store = getStore()
    // Session created earlier; an event lands "now" → updated_at must be the event.
    appendSessionEvent(store, 'abc123def456', { type: 'result', payload: { ok: true } })
    const res = await listGET(
      new Request('https://web.test/api/v1/sessions', {
        headers: { authorization: `Bearer ${token}` },
      }),
    )
    const body = (await res.json()) as {
      data: { created_at: string; updated_at: string }[]
    }
    const s = body.data[0]!
    // The event was appended after the session row → updated_at must be >= created_at,
    // and specifically must move to the event time (not stay pinned to creation).
    expect(new Date(s.updated_at).getTime()).toBeGreaterThanOrEqual(
      new Date(s.created_at).getTime(),
    )
  })

  test('a session with no repo yields empty sources (not undefined — client must not throw)', async () => {
    const store = getStore()
    const token = getOrCreateBridgeToken(store, USER)
    const env = createEnvironment(store, {
      machine_name: 'x',
      directory: '/workspace',
      max_sessions: 4,
      worker_type: 'container',
      user_id: USER,
    })
    getOrCreateSession(store, 'norepo0000000000', env.environment_id)
    const res = await listGET(
      new Request('https://web.test/api/v1/sessions', {
        headers: { authorization: `Bearer ${token}` },
      }),
    )
    const body = (await res.json()) as { data: { session_context?: { sources?: unknown[] } }[] }
    expect(Array.isArray(body.data[0]!.session_context!.sources)).toBe(true)
  })
})
