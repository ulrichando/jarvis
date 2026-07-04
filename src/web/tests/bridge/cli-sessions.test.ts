import { describe, expect, test, beforeEach } from 'vitest'
import { _resetForTests, getStore } from '@/lib/bridge/db'
import {
  getOrCreateBridgeToken,
  listEnvironments,
  listInboundSince,
  listSessionEvents,
  listSessions,
} from '@/lib/bridge/store'
import { GET, POST } from '@/app/api/bridge/v1/cli/sessions/route'

// The `jarvis cloud` / `jarvis teleport` CLI-facing session API: strict
// bridge-token gate (same as /keys), idempotent per-(user,repo) env ensure,
// and a session seeded exactly like the web tasks route's container branch.

const USER = '00000000-0000-0000-0000-000000000042'
const URL_ = 'http://web.test/api/bridge/v1/cli/sessions'

beforeEach(() => {
  _resetForTests()
})

function req(init: { token?: string; method?: string; body?: unknown } = {}): Request {
  return new Request(URL_, {
    method: init.method ?? 'GET',
    headers: {
      ...(init.token ? { authorization: `Bearer ${init.token}` } : {}),
      ...(init.body !== undefined ? { 'content-type': 'application/json' } : {}),
    },
    ...(init.body !== undefined ? { body: JSON.stringify(init.body) } : {}),
  })
}

describe('CLI sessions route (jarvis cloud / teleport picker)', () => {
  test('both verbs 401 without a valid bridge token', async () => {
    expect((await GET(req())).status).toBe(401)
    expect((await GET(req({ token: 'jbr_bogus' }))).status).toBe(401)
    expect(
      (await POST(req({ method: 'POST', body: { repo: 'owner/demo', prompt: 'x' } }))).status,
    ).toBe(401)
  })

  test('POST validates repo shape and prompt', async () => {
    const token = getOrCreateBridgeToken(getStore(), USER)
    const bad = await POST(
      req({ token, method: 'POST', body: { repo: '../evil', prompt: 'x' } }),
    )
    expect(bad.status).toBe(400)
    const noPrompt = await POST(
      req({ token, method: 'POST', body: { repo: 'owner/demo', prompt: '  ' } }),
    )
    expect(noPrompt.status).toBe(400)
  })

  test('POST ensures the per-repo env once, seeds the session, GET lists it', async () => {
    const store = getStore()
    const token = getOrCreateBridgeToken(store, USER)

    const r1 = await POST(
      req({
        token,
        method: 'POST',
        body: { repo: 'owner/demo', prompt: 'fix the flaky test', model: 'claude-fable-5' },
      }),
    )
    expect(r1.status).toBe(200)
    const j1 = (await r1.json()) as { session_id: string; url: string }
    expect(j1.session_id).toMatch(/^[0-9a-f]{16}$/)
    expect(j1.url).toContain(`/code/session_${j1.session_id}`)

    // Env created for (user, repo) with container worker_type…
    const envs = listEnvironments(store, USER).filter(
      (e) => e.worker_type === 'container' && e.git_repo_url === 'https://github.com/owner/demo',
    )
    expect(envs.length).toBe(1)

    // …prompt surfaced as the first event AND seeded inbound; model seeded too.
    const events = listSessionEvents(store, j1.session_id, 0)
    expect(events.some((e) => e.type === 'user_prompt' && e.payload_json.includes('flaky'))).toBe(true)
    const inbound = listInboundSince(store, j1.session_id, 0)
    const flat = inbound.map((i) => i.payload_json).join('\n')
    expect(flat).toContain('fix the flaky test')
    expect(flat).toContain('set_model')
    expect(flat).toContain('claude-fable-5')

    // Second POST for the same repo REUSES the env (idempotent), new session.
    const r2 = await POST(
      req({ token, method: 'POST', body: { repo: 'owner/demo', prompt: 'second task' } }),
    )
    expect(r2.status).toBe(200)
    expect(
      listEnvironments(store, USER).filter(
        (e) => e.git_repo_url === 'https://github.com/owner/demo',
      ).length,
    ).toBe(1)

    // GET lists both sessions for the picker, repo resolved to owner/name.
    const list = await GET(req({ token }))
    expect(list.status).toBe(200)
    const rows = ((await list.json()) as { sessions: { session_id: string; repo: string }[] })
      .sessions
    expect(rows.length).toBe(2)
    expect(rows.every((s) => s.repo === 'owner/demo')).toBe(true)
    expect(listSessions(store, USER).length).toBe(2)
  })
})
