import { describe, expect, test, beforeEach, vi } from 'vitest'
import { _resetForTests, getStore } from '@/lib/bridge/db'
import {
  appendSessionEvent,
  createEnvironment,
  getOrCreateBridgeToken,
  getOrCreateSession,
  setSessionContainer,
  setSessionToken,
} from '@/lib/bridge/store'

// ── Finding #10: bridge auth unification — CHARACTERIZATION matrix ────────────
//
// This test records the CURRENT accept/reject contract of the session-scoped
// bridge routes that share the hand-rolled `extractBearer → resolveBridgeToken
// / validateEnvSecret / isSharedLocalToken` ladder, so the #10 refactor
// (folding those inline gates into one `authorizeBridgeRequest`) can be proven
// BEHAVIOR-IDENTICAL. Every row here must stay green before AND after the
// conversion — a drift means a live caller (the /code cookie poller or the CLI
// worker) just broke.
//
// Three distinct scopes appear across the session routes:
//   session-owner      — a bearer of ANY shape bypasses (v1-permissive: the
//                        proxy shared-token gate already fronts these), else the
//                        browser cookie must own the session's environment.
//                        Routes: diff GET, plan GET/POST, pr-status GET,
//                        pins GET/POST, pr POST, sessions/[id] PATCH/DELETE.
//   session-read       — a bearer must be a VALID session credential (ingress /
//                        env-secret / owning per-user token / shared infra),
//                        else the cookie must own the environment. Route:
//                        events GET (finding #2 closed the presence-only hole).
//   session-credential — a bearer must be a VALID session credential; NO cookie
//                        path. Routes: events POST, archive POST.
//
// getUserId is the browser-cookie identity (the /code poller sends no bearer,
// just the same-origin session cookie). Drive it via a mutable holder.
let currentUser: string | null = null
vi.mock('@/lib/auth-helpers', () => ({
  getUserId: async () => currentUser,
}))

const OWNER = '00000000-0000-0000-0000-0000000000a1'
const OTHER = '00000000-0000-0000-0000-0000000000b2'

// Seed an owned environment + a container session bound to it, plus every
// credential a legitimate reader/writer might present. A container_json is set
// so the owner-scope routes that read container meta (diff/pr/pr-status) don't
// throw before the auth check is even reached.
function seedOwnedSession(sessionId: string) {
  const store = getStore()
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
  setSessionContainer(store, sessionId, {
    container: `jarvis-code-${sessionId}`,
    repo: 'o/r',
  })
  appendSessionEvent(store, sessionId, {
    type: 'user_prompt',
    payload: { text: 'secret transcript' },
  })
  const ownerToken = getOrCreateBridgeToken(store, OWNER)
  const otherToken = getOrCreateBridgeToken(store, OTHER)
  return { environment_id, environment_secret, ingress, ownerToken, otherToken }
}

const ctx = (sessionId: string) => ({ params: Promise.resolve({ sessionId }) })

function req(
  path: string,
  method: 'GET' | 'POST' | 'PATCH' | 'DELETE',
  bearer?: string,
  body?: unknown,
): Request {
  const headers: Record<string, string> = {}
  if (bearer) headers.authorization = `Bearer ${bearer}`
  const init: RequestInit = { method, headers }
  if (body !== undefined) {
    headers['content-type'] = 'application/json'
    init.body = JSON.stringify(body)
  }
  return new Request(`http://127.0.0.1:3000${path}`, init)
}

beforeEach(() => {
  _resetForTests()
  currentUser = null
  delete process.env.JARVIS_LOCAL_API_TOKEN
})

// A single description of every session-scoped route + method under test, with
// the request it takes. `authorized` is the status expected when a legitimate
// credential is present; the per-scope credential variations are exercised in
// the scope-grouped describes below.
type RouteCase = {
  name: string
  scope: 'session-owner' | 'session-read' | 'session-credential'
  importPath: string
  fn: 'GET' | 'POST' | 'PATCH' | 'DELETE'
  make: (sid: string, bearer?: string) => Request
  okStatus: number | number[]
}

const okIn = (status: number, ok: number | number[]) =>
  Array.isArray(ok) ? ok.includes(status) : status === ok

// eslint-disable-next-line @typescript-eslint/no-explicit-any
async function callRoute(rc: RouteCase, sid: string, bearer?: string) {
  const mod = (await import(rc.importPath)) as Record<string, unknown>
  const handler = mod[rc.fn] as (
    r: Request,
    c: ReturnType<typeof ctx>,
  ) => Promise<Response>
  return handler(rc.make(sid, bearer), ctx(sid))
}

// ── session-owner scope: bearer-of-any-shape bypasses; else cookie ownership ──
const OWNER_ROUTES: RouteCase[] = [
  {
    name: 'diff GET',
    scope: 'session-owner',
    importPath: '@/app/api/bridge/v1/sessions/[sessionId]/diff/route',
    fn: 'GET',
    make: (sid, b) => req(`/api/bridge/v1/sessions/${sid}/diff`, 'GET', b),
    okStatus: 200,
  },
  {
    name: 'plan GET',
    scope: 'session-owner',
    importPath: '@/app/api/bridge/v1/sessions/[sessionId]/plan/route',
    fn: 'GET',
    make: (sid, b) => req(`/api/bridge/v1/sessions/${sid}/plan`, 'GET', b),
    okStatus: 200,
  },
  {
    name: 'pins GET',
    scope: 'session-owner',
    importPath: '@/app/api/bridge/v1/sessions/[sessionId]/pins/route',
    fn: 'GET',
    make: (sid, b) => req(`/api/bridge/v1/sessions/${sid}/pins`, 'GET', b),
    okStatus: 200,
  },
  {
    name: 'pins POST',
    scope: 'session-owner',
    importPath: '@/app/api/bridge/v1/sessions/[sessionId]/pins/route',
    fn: 'POST',
    make: (sid, b) =>
      req(`/api/bridge/v1/sessions/${sid}/pins`, 'POST', b, {
        uuid: 'u1',
        pinned: true,
      }),
    okStatus: 200,
  },
  {
    name: 'sessions/[id] PATCH',
    scope: 'session-owner',
    importPath: '@/app/api/bridge/v1/sessions/[sessionId]/route',
    fn: 'PATCH',
    make: (sid, b) =>
      req(`/api/bridge/v1/sessions/${sid}`, 'PATCH', b, { pinned: true }),
    okStatus: 200,
  },
  {
    name: 'sessions/[id] DELETE',
    scope: 'session-owner',
    importPath: '@/app/api/bridge/v1/sessions/[sessionId]/route',
    fn: 'DELETE',
    make: (sid, b) => req(`/api/bridge/v1/sessions/${sid}`, 'DELETE', b),
    okStatus: 204,
  },
]

describe('session-owner scope — bearer bypass + cookie ownership', () => {
  for (const rc of OWNER_ROUTES) {
    describe(rc.name, () => {
      test('any bearer (worker, v1-permissive) → authorized', async () => {
        seedOwnedSession('s')
        const res = await callRoute(rc, 's', 'literally-anything')
        expect(okIn(res.status, rc.okStatus)).toBe(true)
      })

      test('owner cookie (no bearer) → authorized', async () => {
        seedOwnedSession('s')
        currentUser = OWNER
        const res = await callRoute(rc, 's')
        expect(okIn(res.status, rc.okStatus)).toBe(true)
      })

      test('cross-user cookie (no bearer) → 403', async () => {
        seedOwnedSession('s')
        currentUser = OTHER
        const res = await callRoute(rc, 's')
        expect(res.status).toBe(403)
      })

      test('anonymous (no bearer, no cookie) → 401', async () => {
        seedOwnedSession('s')
        currentUser = null
        const res = await callRoute(rc, 's')
        expect(res.status).toBe(401)
      })

      test('unknown session, no bearer → 404', async () => {
        const res = await callRoute(rc, 'nope')
        expect(res.status).toBe(404)
      })
    })
  }
})

// ── session-read scope: bearer must be a VALID credential; else cookie owner ──
describe('session-read scope (events GET) — credential OR cookie ownership', () => {
  const importPath = '@/app/api/bridge/v1/sessions/[sessionId]/events/route'
  const make = (sid: string, b?: string) =>
    req(`/api/bridge/v1/sessions/${sid}/events?since=0`, 'GET', b)
  async function get(sid: string, b?: string) {
    const { GET } = await import(importPath)
    return GET(make(sid, b), ctx(sid))
  }

  test('ingress token → 200', async () => {
    const { ingress } = seedOwnedSession('s')
    expect((await get('s', ingress)).status).toBe(200)
  })
  test('env secret → 200', async () => {
    const { environment_secret } = seedOwnedSession('s')
    expect((await get('s', environment_secret)).status).toBe(200)
  })
  test('owner per-user token → 200', async () => {
    const { ownerToken } = seedOwnedSession('s')
    expect((await get('s', ownerToken)).status).toBe(200)
  })
  test('shared infra token → 200', async () => {
    seedOwnedSession('s')
    process.env.JARVIS_LOCAL_API_TOKEN = 'shared'
    expect((await get('s', 'shared')).status).toBe(200)
  })
  test('another user token → 401 (IDOR)', async () => {
    const { otherToken } = seedOwnedSession('s')
    expect((await get('s', otherToken)).status).toBe(401)
  })
  test('junk bearer → 401', async () => {
    seedOwnedSession('s')
    expect((await get('s', 'garbage')).status).toBe(401)
  })
  test('owner cookie, no bearer → 200', async () => {
    seedOwnedSession('s')
    currentUser = OWNER
    expect((await get('s')).status).toBe(200)
  })
  test('cross-user cookie, no bearer → 403', async () => {
    seedOwnedSession('s')
    currentUser = OTHER
    expect((await get('s')).status).toBe(403)
  })
  test('anonymous → 401', async () => {
    seedOwnedSession('s')
    expect((await get('s')).status).toBe(401)
  })
  test('unknown session, anonymous → 404', async () => {
    expect((await get('nope')).status).toBe(404)
  })
})

// ── session-credential scope: valid bearer only, NO cookie path ───────────────
describe('session-credential scope (events POST, archive POST) — bearer only', () => {
  const CRED_ROUTES: RouteCase[] = [
    {
      name: 'events POST',
      scope: 'session-credential',
      importPath: '@/app/api/bridge/v1/sessions/[sessionId]/events/route',
      fn: 'POST',
      make: (sid, b) =>
        req(`/api/bridge/v1/sessions/${sid}/events`, 'POST', b, {
          events: [{ type: 'user_prompt' }],
        }),
      okStatus: 204,
    },
    {
      name: 'archive POST',
      scope: 'session-credential',
      importPath: '@/app/api/bridge/v1/sessions/[sessionId]/archive/route',
      fn: 'POST',
      make: (sid, b) => req(`/api/bridge/v1/sessions/${sid}/archive`, 'POST', b),
      okStatus: [204, 409],
    },
  ]

  for (const rc of CRED_ROUTES) {
    describe(rc.name, () => {
      test('ingress token → authorized', async () => {
        const { ingress } = seedOwnedSession('s')
        const res = await callRoute(rc, 's', ingress)
        expect(okIn(res.status, rc.okStatus)).toBe(true)
      })
      test('env secret → authorized', async () => {
        const { environment_secret } = seedOwnedSession('s')
        const res = await callRoute(rc, 's', environment_secret)
        expect(okIn(res.status, rc.okStatus)).toBe(true)
      })
      test('owner per-user token → authorized', async () => {
        const { ownerToken } = seedOwnedSession('s')
        const res = await callRoute(rc, 's', ownerToken)
        expect(okIn(res.status, rc.okStatus)).toBe(true)
      })
      test('shared infra token → authorized', async () => {
        seedOwnedSession('s')
        process.env.JARVIS_LOCAL_API_TOKEN = 'shared'
        const res = await callRoute(rc, 's', 'shared')
        expect(okIn(res.status, rc.okStatus)).toBe(true)
      })
      test('another user token → 401', async () => {
        const { otherToken } = seedOwnedSession('s')
        const res = await callRoute(rc, 's', otherToken)
        expect(res.status).toBe(401)
      })
      test('junk bearer → 401', async () => {
        seedOwnedSession('s')
        const res = await callRoute(rc, 's', 'garbage')
        expect(res.status).toBe(401)
      })
      test('no bearer (cookie has no effect) → 401', async () => {
        seedOwnedSession('s')
        currentUser = OWNER
        const res = await callRoute(rc, 's')
        expect(res.status).toBe(401)
      })
    })
  }
})
