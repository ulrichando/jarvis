import { describe, expect, test, beforeEach, vi } from 'vitest'
import { _resetForTests, getStore } from '@/lib/bridge/db'
import {
  createEnvironment,
  getOrCreateSession,
  setSessionContainer,
} from '@/lib/bridge/store'

// M2: external bot-job sessions (gh-app dispatch) carry an App installation
// token in their container meta — the pr-status route must authenticate the
// GitHub lookup with THAT token, not the box owner's PAT. Normal /code
// sessions (no token in meta) keep today's stored-PAT path byte-identical.

vi.mock('@/lib/auth-helpers', () => ({
  getUserId: vi.fn(async () => '00000000-0000-0000-0000-000000000001'),
}))
vi.mock('@/lib/connectors/github', () => ({
  githubPrStatus: vi.fn(async () => ({
    ok: true,
    status: {
      pr: { number: 7, url: 'https://github.com/owner/demo/pull/7', state: 'open', draft: false },
      checks: null,
      sha: null,
    },
  })),
}))

function makeContainerSession(sessionId: string, meta: Record<string, unknown>): void {
  const store = getStore()
  const env = createEnvironment(store, {
    machine_name: 'Cloud container',
    directory: '/workspace',
    git_repo_url: 'https://github.com/owner/demo',
    max_sessions: 4,
    worker_type: 'container',
    user_id: '00000000-0000-0000-0000-000000000001',
  })
  getOrCreateSession(store, sessionId, env.environment_id)
  setSessionContainer(store, sessionId, {
    container: `jarvis-code-${sessionId}`,
    repo: 'owner/demo',
    ...meta,
  })
}

// The worker-style bearer short-circuits authorizeSession (v1-permissive),
// keeping the test focused on the token-threading behavior under test.
function get(sessionId: string, branch: string): Request {
  return new Request(
    `http://127.0.0.1:3000/api/bridge/v1/sessions/${sessionId}/pr-status?branch=${encodeURIComponent(branch)}`,
    { headers: { authorization: 'Bearer worker-token' } },
  )
}

async function route() {
  return import('@/app/api/bridge/v1/sessions/[sessionId]/pr-status/route')
}

beforeEach(() => {
  _resetForTests()
  vi.clearAllMocks()
})

describe('GET /api/bridge/v1/sessions/{id}/pr-status', () => {
  test('external bot-job session → githubPrStatus authenticates with the injected installation token', async () => {
    makeContainerSession('feed00aa11223344', { installationToken: 'ghs_inst_tok', botLogin: 'jvs' })
    const { GET } = await route()
    const res = await GET(get('feed00aa11223344', 'jarvis/session-x'), {
      params: Promise.resolve({ sessionId: 'feed00aa11223344' }),
    })
    expect(res.status).toBe(200)
    const j = (await res.json()) as { pr: { number: number } | null; repo: string }
    expect(j.pr?.number).toBe(7)
    expect(j.repo).toBe('owner/demo')
    const { githubPrStatus } = await import('@/lib/connectors/github')
    expect(vi.mocked(githubPrStatus)).toHaveBeenCalledWith('owner/demo', 'jarvis/session-x', 'ghs_inst_tok')
  })

  test('regression: normal /code session (no token in meta) calls githubPrStatus exactly as today', async () => {
    makeContainerSession('feed00bb11223344', {})
    const { GET } = await route()
    const res = await GET(get('feed00bb11223344', 'jarvis/session-y'), {
      params: Promise.resolve({ sessionId: 'feed00bb11223344' }),
    })
    expect(res.status).toBe(200)
    const { githubPrStatus } = await import('@/lib/connectors/github')
    // 2 args — NO trailing token argument on the normal path.
    expect(vi.mocked(githubPrStatus)).toHaveBeenCalledWith('owner/demo', 'jarvis/session-y')
  })

  test('no branch → empty status without hitting GitHub', async () => {
    makeContainerSession('feed00cc11223344', {})
    const { GET } = await route()
    const res = await GET(
      new Request('http://127.0.0.1:3000/api/bridge/v1/sessions/feed00cc11223344/pr-status', {
        headers: { authorization: 'Bearer worker-token' },
      }),
      { params: Promise.resolve({ sessionId: 'feed00cc11223344' }) },
    )
    expect(res.status).toBe(200)
    expect(await res.json()).toEqual({ pr: null, checks: null, sha: null, repo: null })
    const { githubPrStatus } = await import('@/lib/connectors/github')
    expect(vi.mocked(githubPrStatus)).not.toHaveBeenCalled()
  })
})
