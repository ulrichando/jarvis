import { describe, expect, test, beforeEach, vi } from 'vitest'
import { _resetForTests, getStore } from '@/lib/bridge/db'
import { createEnvironment, getOrCreateSession } from '@/lib/bridge/store'

vi.mock('@/lib/auth-helpers', () => ({
  getUserId: async () => '00000000-0000-0000-0000-000000000001',
  getUserIdOrSharedLocal: async () => '00000000-0000-0000-0000-000000000001',
}))

const USER = '00000000-0000-0000-0000-000000000001'

beforeEach(() => {
  _resetForTests()
})

function makeEnv() {
  return createEnvironment(getStore(), {
    machine_name: 'gh-app-bot',
    directory: '/workspace',
    git_repo_url: 'https://github.com/o/r',
    max_sessions: 4,
    worker_type: 'container',
    user_id: USER,
  })
}

const evt = (id: string, sid: string, type: string, at: number) =>
  getStore()
    .db.prepare(
      'INSERT INTO session_events (event_id, session_id, type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)',
    )
    .run(id, sid, type, '{}', at)

// The sidebar renders "N ago" from each session's LAST activity, not its
// creation time — a long session you just used must read as recent. Regression
// guard for the "active 29m ago when I just finished it" report.
describe('GET /api/bridge/v1/sessions — last_activity_at', () => {
  test('reflects the newest event, not created_at', async () => {
    const store = getStore()
    const env = makeEnv()
    const created = Date.now() - 40 * 60_000 // session started 40 min ago
    const lastEvt = Date.now() - 5 * 60_000 // last activity 5 min ago
    getOrCreateSession(store, 's_active', env.environment_id, 'try to redesign it again')
    store.db.prepare('UPDATE sessions SET created_at = ? WHERE session_id = ?').run(created, 's_active')
    evt('e1', 's_active', 'user_prompt', created)
    evt('e2', 's_active', 'assistant', lastEvt)

    const { GET } = await import('@/app/api/bridge/v1/sessions/route')
    const res = await GET(new Request('http://127.0.0.1:3000/api/bridge/v1/sessions'))
    const body = (await res.json()) as {
      sessions: Array<{ session_id: string; created_at: number; last_activity_at: number }>
    }
    const s = body.sessions.find((x) => x.session_id === 's_active')
    expect(s).toBeTruthy()
    expect(s!.created_at).toBe(created)
    expect(s!.last_activity_at).toBe(lastEvt) // newest event, not creation time
  })

  test('falls back to created_at when the session has no events', async () => {
    const store = getStore()
    const env = makeEnv()
    getOrCreateSession(store, 's_bare', env.environment_id, 'no events yet')
    const row = store.db
      .prepare('SELECT created_at FROM sessions WHERE session_id = ?')
      .get('s_bare') as { created_at: number }

    const { GET } = await import('@/app/api/bridge/v1/sessions/route')
    const res = await GET(new Request('http://127.0.0.1:3000/api/bridge/v1/sessions'))
    const body = (await res.json()) as {
      sessions: Array<{ session_id: string; last_activity_at: number }>
    }
    const s = body.sessions.find((x) => x.session_id === 's_bare')
    expect(s!.last_activity_at).toBe(row.created_at)
  })
})
