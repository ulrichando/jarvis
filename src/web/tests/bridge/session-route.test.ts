import { describe, expect, test, beforeEach } from 'vitest'
import { _resetForTests, getStore } from '@/lib/bridge/db'
import {
  createEnvironment,
  getOrCreateSession,
  mergeWorkerState,
} from '@/lib/bridge/store'

// Phase C (gh-app × /code): the gh-app worker polls
// GET /api/bridge/v1/sessions/{id} for the run state — the response must
// carry `status` (ccrSessionStatus: worker_state_json.worker_status →
// running/idle/requires_action, archived rows → archived). Additive field:
// the CLI's existing readers (environment_id/title) stay untouched.

function makeSession(sessionId: string): void {
  const store = getStore()
  const env = createEnvironment(store, {
    machine_name: 'gh-app-bot',
    directory: '/workspace',
    git_repo_url: 'https://github.com/owner/demo',
    max_sessions: 4,
    worker_type: 'container',
    user_id: '00000000-0000-0000-0000-000000000001',
  })
  getOrCreateSession(store, sessionId, env.environment_id, 'bot job')
}

const get = (sessionId: string) =>
  new Request(`http://127.0.0.1:3000/api/bridge/v1/sessions/${sessionId}`)
const ctx = (sessionId: string) => ({ params: Promise.resolve({ sessionId }) })

async function route() {
  return import('@/app/api/bridge/v1/sessions/[sessionId]/route')
}

beforeEach(() => {
  _resetForTests()
})

describe('GET /api/bridge/v1/sessions/{id} — status field (gh-app poller)', () => {
  test('fresh session (no worker state yet) → status "running" (poll keeps going)', async () => {
    makeSession('s_fresh')
    const { GET } = await route()
    const res = await GET(get('s_fresh'), ctx('s_fresh'))
    expect(res.status).toBe(200)
    const body = (await res.json()) as Record<string, unknown>
    expect(body.status).toBe('running')
    // Existing readers unaffected — the additive field sits alongside them.
    expect(body.id).toBe('s_fresh')
    expect(typeof body.environment_id).toBe('string')
    expect(body.title).toBe('bot job')
    expect(body.archived).toBe(false)
  })

  test('worker_status idle → status "idle" (the done signal)', async () => {
    makeSession('s_idle')
    mergeWorkerState(getStore(), 's_idle', { worker_status: 'idle' })
    const { GET } = await route()
    const body = (await (await GET(get('s_idle'), ctx('s_idle'))).json()) as {
      status?: string
    }
    expect(body.status).toBe('idle')
  })

  test('worker_status requires_action → status "requires_action"', async () => {
    makeSession('s_act')
    mergeWorkerState(getStore(), 's_act', { worker_status: 'requires_action' })
    const { GET } = await route()
    const body = (await (await GET(get('s_act'), ctx('s_act'))).json()) as {
      status?: string
    }
    expect(body.status).toBe('requires_action')
  })

  test('unknown session still 404s', async () => {
    const { GET } = await route()
    expect((await GET(get('nope'), ctx('nope'))).status).toBe(404)
  })
})
