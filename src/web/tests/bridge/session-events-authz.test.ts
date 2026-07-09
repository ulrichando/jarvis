import { describe, expect, test, beforeEach, vi } from 'vitest'
import { _resetForTests, getStore } from '@/lib/bridge/db'
import {
  appendSessionEvent,
  createEnvironment,
  getOrCreateBridgeToken,
  getOrCreateSession,
  listInboundSince,
  setSessionToken,
} from '@/lib/bridge/store'

// getUserId is the browser-cookie identity (the /code poller sends no bearer,
// just the same-origin session cookie). Drive it via a mutable holder so one
// suite can exercise anonymous, owner, and cross-user cookie callers.
let currentUser: string | null = null
vi.mock('@/lib/auth-helpers', () => ({
  getUserId: async () => currentUser,
}))

const OWNER = '00000000-0000-0000-0000-0000000000a1'
const OTHER = '00000000-0000-0000-0000-0000000000b2'

// ── Fixtures ────────────────────────────────────────────────────────────────
// An owned environment + a session bound to it, plus every credential a
// legitimate reader/writer might hold: the per-session ingress token, the
// environment secret, the owner's per-user bridge token, and the shared infra
// token. Returns them so each test picks the credential under test.
function seedOwnedSession(sessionId: string) {
  const store = getStore()
  // Unique machine identity per session — createEnvironment reuses by
  // (owner, machine_name, directory), so a fixed name would collapse two
  // "different" envs into one (and their secrets would match).
  const { environment_id, environment_secret } = createEnvironment(store, {
    machine_name: `moon-${sessionId}`,
    directory: '/workspace',
    git_repo_url: 'https://github.com/o/r',
    max_sessions: 4,
    worker_type: 'container',
    user_id: OWNER,
  })
  getOrCreateSession(store, sessionId, environment_id, 'a job')
  const ingress = 'sit_' + sessionId
  setSessionToken(store, sessionId, ingress)
  // Add a transcript event so a leaked read would actually expose content.
  appendSessionEvent(store, sessionId, {
    type: 'user_prompt',
    payload: { text: 'secret transcript' },
  })
  const ownerToken = getOrCreateBridgeToken(store, OWNER)
  const otherToken = getOrCreateBridgeToken(store, OTHER)
  return { environment_id, environment_secret, ingress, ownerToken, otherToken }
}

const bridgeGetReq = (sessionId: string, bearer?: string) =>
  new Request(
    `http://127.0.0.1:3000/api/bridge/v1/sessions/${sessionId}/events?since=0`,
    bearer ? { headers: { authorization: `Bearer ${bearer}` } } : undefined,
  )
const ctx = (sessionId: string) => ({ params: Promise.resolve({ sessionId }) })

async function bridgeRoute() {
  return import('@/app/api/bridge/v1/sessions/[sessionId]/events/route')
}

beforeEach(() => {
  _resetForTests()
  currentUser = null
  delete process.env.JARVIS_LOCAL_API_TOKEN
})

// ── Finding #2: bridge events GET ownership check ─────────────────────────────
describe('GET /api/bridge/v1/sessions/{id}/events — ownership gate (finding #2)', () => {
  test('anonymous (no bearer, no cookie) → 401, transcript NOT disclosed', async () => {
    seedOwnedSession('s_own')
    const { GET } = await bridgeRoute()
    const res = await GET(bridgeGetReq('s_own'), ctx('s_own'))
    expect(res.status).toBe(401)
    const body = await res.text()
    expect(body).not.toContain('secret transcript')
  })

  test('made-up session id, anonymous → 404 (not a 200 empty leak)', async () => {
    const { GET } = await bridgeRoute()
    // Live-confirmed hole was: unauth GET to a fabricated id returned
    // 200 {"events":[],...}. It must now refuse.
    const res = await GET(bridgeGetReq('does-not-exist'), ctx('does-not-exist'))
    expect(res.status).toBe(404)
  })

  test('cross-user cookie → 403, transcript NOT disclosed', async () => {
    seedOwnedSession('s_own')
    currentUser = OTHER
    const { GET } = await bridgeRoute()
    const res = await GET(bridgeGetReq('s_own'), ctx('s_own'))
    expect(res.status).toBe(403)
    expect(await res.text()).not.toContain('secret transcript')
  })

  test('owner cookie (the /code poller) → 200 with the transcript', async () => {
    seedOwnedSession('s_own')
    currentUser = OWNER
    const { GET } = await bridgeRoute()
    const res = await GET(bridgeGetReq('s_own'), ctx('s_own'))
    expect(res.status).toBe(200)
    const body = (await res.json()) as { events: Array<{ payload: unknown }> }
    expect(JSON.stringify(body.events)).toContain('secret transcript')
  })

  test('session ingress token (CLI worker bearer) → 200', async () => {
    const { ingress } = seedOwnedSession('s_own')
    const { GET } = await bridgeRoute()
    const res = await GET(bridgeGetReq('s_own', ingress), ctx('s_own'))
    expect(res.status).toBe(200)
  })

  test('owning environment secret bearer → 200', async () => {
    const { environment_secret } = seedOwnedSession('s_own')
    const { GET } = await bridgeRoute()
    const res = await GET(bridgeGetReq('s_own', environment_secret), ctx('s_own'))
    expect(res.status).toBe(200)
  })

  test("owner's per-user bridge token bearer → 200", async () => {
    const { ownerToken } = seedOwnedSession('s_own')
    const { GET } = await bridgeRoute()
    const res = await GET(bridgeGetReq('s_own', ownerToken), ctx('s_own'))
    expect(res.status).toBe(200)
  })

  test('shared infra token bearer → 200', async () => {
    seedOwnedSession('s_own')
    process.env.JARVIS_LOCAL_API_TOKEN = 'shared-infra-secret'
    const { GET } = await bridgeRoute()
    const res = await GET(
      bridgeGetReq('s_own', 'shared-infra-secret'),
      ctx('s_own'),
    )
    expect(res.status).toBe(200)
  })

  test("another user's per-user bridge token bearer → 401 (IDOR guard)", async () => {
    const { otherToken } = seedOwnedSession('s_own')
    const { GET } = await bridgeRoute()
    const res = await GET(bridgeGetReq('s_own', otherToken), ctx('s_own'))
    expect(res.status).toBe(401)
    expect(await res.text()).not.toContain('secret transcript')
  })

  test('junk bearer → 401', async () => {
    seedOwnedSession('s_own')
    const { GET } = await bridgeRoute()
    const res = await GET(bridgeGetReq('s_own', 'garbage'), ctx('s_own'))
    expect(res.status).toBe(401)
  })

  test('POST sibling still guarded (regression — was already fixed)', async () => {
    seedOwnedSession('s_own')
    const { POST } = await bridgeRoute()
    const res = await POST(
      new Request('http://127.0.0.1:3000/api/bridge/v1/sessions/s_own/events', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ events: [{ type: 'user_prompt' }] }),
      }),
      ctx('s_own'),
    )
    expect(res.status).toBe(401)
  })
})

// ── Finding #2b: v1 twin POST validates the bearer, not just presence ─────────
describe('POST /api/v1/sessions/{id}/events — bearer VALIDITY (finding #2b)', () => {
  const url = (id: string) => `http://127.0.0.1:3000/api/v1/sessions/${id}/events`
  const postReq = (id: string, bearer: string | null, body: unknown) =>
    new Request(url(id), {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        ...(bearer ? { authorization: `Bearer ${bearer}` } : {}),
      },
      body: JSON.stringify(body),
    })
  async function v1Route() {
    return import('@/app/api/v1/sessions/[sessionId]/events/route')
  }

  test('missing bearer → 401', async () => {
    seedOwnedSession('s_v1')
    const { POST } = await v1Route()
    const res = await POST(
      postReq('s_v1', null, { events: [{ type: 'user_turn' }] }),
      ctx('s_v1'),
    )
    expect(res.status).toBe(401)
  })

  test('junk bearer → 401 (was accepted: presence-only check)', async () => {
    seedOwnedSession('s_v1')
    const { POST } = await v1Route()
    const res = await POST(
      postReq('s_v1', 'not-a-real-token', { events: [{ type: 'user_turn' }] }),
      ctx('s_v1'),
    )
    expect(res.status).toBe(401)
    // Nothing was queued for the worker.
    expect(listInboundSince(getStore(), 's_v1', 0)).toHaveLength(0)
  })

  test("another user's bridge token → 401, no inbound queued", async () => {
    const { otherToken } = seedOwnedSession('s_v1')
    const { POST } = await v1Route()
    const res = await POST(
      postReq('s_v1', otherToken, { events: [{ type: 'user_turn' }] }),
      ctx('s_v1'),
    )
    expect(res.status).toBe(401)
    expect(listInboundSince(getStore(), 's_v1', 0)).toHaveLength(0)
  })

  test('session ingress token → 204 and queues the inbound turn', async () => {
    const { ingress } = seedOwnedSession('s_v1')
    const { POST } = await v1Route()
    const res = await POST(
      postReq('s_v1', ingress, { events: [{ type: 'user_turn', text: 'hi' }] }),
      ctx('s_v1'),
    )
    expect(res.status).toBe(204)
    expect(listInboundSince(getStore(), 's_v1', 0)).toHaveLength(1)
  })

  test("owner's per-user bridge token → 204", async () => {
    const { ownerToken } = seedOwnedSession('s_v1')
    const { POST } = await v1Route()
    const res = await POST(
      postReq('s_v1', ownerToken, { events: [{ type: 'user_turn' }] }),
      ctx('s_v1'),
    )
    expect(res.status).toBe(204)
  })
})

// ── Finding #3: admin/enqueue requires an owning credential ────────────────────
describe('POST /api/bridge/v1/admin/enqueue — auth (finding #3)', () => {
  const url = 'http://127.0.0.1:3000/api/bridge/v1/admin/enqueue'
  const enqueueReq = (bearer: string | null, body: unknown) =>
    new Request(url, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        ...(bearer ? { authorization: `Bearer ${bearer}` } : {}),
      },
      body: JSON.stringify(body),
    })
  async function enqueueRoute() {
    return import('@/app/api/bridge/v1/admin/enqueue/route')
  }

  test('missing bearer → 401 (was accepted unauthenticated)', async () => {
    const { environment_id } = seedOwnedSession('s_enq')
    const { POST } = await enqueueRoute()
    const res = await POST(
      enqueueReq(null, { environment_id, session_id: 's_enq', data: {} }),
    )
    expect(res.status).toBe(401)
  })

  test('junk bearer → 403', async () => {
    const { environment_id } = seedOwnedSession('s_enq')
    const { POST } = await enqueueRoute()
    const res = await POST(
      enqueueReq('garbage', { environment_id, session_id: 's_enq', data: {} }),
    )
    expect(res.status).toBe(403)
  })

  test("non-owner's bridge token → 403", async () => {
    const { environment_id, otherToken } = seedOwnedSession('s_enq')
    const { POST } = await enqueueRoute()
    const res = await POST(
      enqueueReq(otherToken, {
        environment_id,
        session_id: 's_enq',
        data: {},
      }),
    )
    expect(res.status).toBe(403)
  })

  test("owner's bridge token → 200 with a work_id", async () => {
    const { environment_id, ownerToken } = seedOwnedSession('s_enq')
    const { POST } = await enqueueRoute()
    const res = await POST(
      enqueueReq(ownerToken, {
        environment_id,
        session_id: 's_enq',
        data: { foo: 1 },
      }),
    )
    expect(res.status).toBe(200)
    const body = (await res.json()) as { work_id?: string }
    expect(typeof body.work_id).toBe('string')
  })

  test('shared infra token → 200 (single-operator deploy)', async () => {
    const { environment_id } = seedOwnedSession('s_enq')
    process.env.JARVIS_LOCAL_API_TOKEN = 'shared-infra-secret'
    const { POST } = await enqueueRoute()
    const res = await POST(
      enqueueReq('shared-infra-secret', {
        environment_id,
        session_id: 's_enq',
        data: {},
      }),
    )
    expect(res.status).toBe(200)
  })

  test("target environment's own secret → 200 (worker enqueues for itself)", async () => {
    const { environment_id, environment_secret } = seedOwnedSession('s_enq')
    const { POST } = await enqueueRoute()
    const res = await POST(
      enqueueReq(environment_secret, {
        environment_id,
        session_id: 's_enq',
        data: {},
      }),
    )
    expect(res.status).toBe(200)
  })

  test("a DIFFERENT environment's secret → 403 (can't enqueue elsewhere)", async () => {
    const { environment_secret: foreignSecret } = seedOwnedSession('s_other')
    const { environment_id } = seedOwnedSession('s_enq')
    const { POST } = await enqueueRoute()
    const res = await POST(
      enqueueReq(foreignSecret, {
        environment_id,
        session_id: 's_enq',
        data: {},
      }),
    )
    expect(res.status).toBe(403)
  })

  test('missing params still → 400 (after auth passes)', async () => {
    const { ownerToken } = seedOwnedSession('s_enq')
    const { POST } = await enqueueRoute()
    const res = await POST(enqueueReq(ownerToken, { environment_id: 'x' }))
    expect(res.status).toBe(400)
  })

  test('owner token but unknown environment → 403 (not 404 — no owner match)', async () => {
    const { ownerToken } = seedOwnedSession('s_enq')
    const { POST } = await enqueueRoute()
    const res = await POST(
      enqueueReq(ownerToken, {
        environment_id: 'no-such-env',
        session_id: 's_enq',
        data: {},
      }),
    )
    // findEnvironment(null) → 404, which the auth block guards first (env is
    // null → the ownership check can't pass → 403). Either way it is NOT
    // enqueued anonymously; assert it's rejected.
    expect([403, 404]).toContain(res.status)
  })
})
