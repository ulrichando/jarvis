import { describe, expect, test, beforeEach, vi } from 'vitest'

// Finding #2: the bridge events GET had NO auth — any logged-in user (or any
// caller past the proxy) could read ANY session's full transcript by iterating
// ids (cross-user IDOR). The GET now requires a valid session credential
// (worker/CLI bearer) OR the logged-in owner of the session's environment (the
// browser /code poller sends a same-origin cookie, no bearer). We control the
// cookie-user resolution by mocking getUserId.
let mockUserId: string | null = null
vi.mock('@/lib/auth-helpers', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/auth-helpers')>()
  return { ...actual, getUserId: async () => mockUserId }
})

import { _resetForTests, getStore } from '@/lib/bridge/db'
import {
  createEnvironment,
  getOrCreateSession,
  setSessionToken,
} from '@/lib/bridge/store'
import { GET } from '@/app/api/bridge/v1/sessions/[sessionId]/events/route'

const OWNER = '00000000-0000-0000-0000-0000000000a1'
const OTHER = '00000000-0000-0000-0000-0000000000b2'

function makeSession(sessionId: string, userId: string, sessionToken?: string) {
  const store = getStore()
  const env = createEnvironment(store, {
    machine_name: `m-${userId}`,
    directory: '/workspace',
    git_repo_url: 'https://github.com/owner/demo',
    max_sessions: 4,
    worker_type: 'container',
    user_id: userId,
  })
  getOrCreateSession(store, sessionId, env.environment_id, 'job')
  if (sessionToken) setSessionToken(store, sessionId, sessionToken)
}

const ctx = (sessionId: string) => ({ params: Promise.resolve({ sessionId }) })
function get(sessionId: string, token?: string): Request {
  return new Request(
    `http://127.0.0.1:3000/api/bridge/v1/sessions/${sessionId}/events?since=0`,
    { headers: token ? { authorization: `Bearer ${token}` } : {} },
  )
}

beforeEach(() => {
  _resetForTests()
  mockUserId = null
})

describe('bridge events GET read-authz (finding #2 — IDOR closed)', () => {
  test('401 with no bearer and no logged-in user (anonymous)', async () => {
    makeSession('sess1', OWNER)
    const r = await GET(get('sess1'), ctx('sess1'))
    expect(r.status).toBe(401)
  })

  test('200 with a valid session-credential bearer', async () => {
    makeSession('sess1', OWNER, 'sess_tok_valid')
    const r = await GET(get('sess1', 'sess_tok_valid'), ctx('sess1'))
    expect(r.status).toBe(200)
  })

  test('401 with a junk bearer', async () => {
    makeSession('sess1', OWNER, 'sess_tok_valid')
    const r = await GET(get('sess1', 'jbr_bogus'), ctx('sess1'))
    expect(r.status).toBe(401)
  })

  test('200 for the environment owner via same-origin cookie', async () => {
    makeSession('sess1', OWNER)
    mockUserId = OWNER
    const r = await GET(get('sess1'), ctx('sess1'))
    expect(r.status).toBe(200)
  })

  test("403 for a different logged-in user's cookie (IDOR blocked)", async () => {
    makeSession('sess1', OWNER)
    mockUserId = OTHER
    const r = await GET(get('sess1'), ctx('sess1'))
    expect(r.status).toBe(403)
  })

  test('404 when the session does not exist (logged-in, no bearer)', async () => {
    mockUserId = OTHER
    const r = await GET(get('ghost'), ctx('ghost'))
    expect(r.status).toBe(404)
  })
})
