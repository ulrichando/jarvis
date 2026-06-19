import { describe, expect, test, beforeEach, afterEach, vi } from 'vitest'
import {
  parseGitRequest,
  assertRepoAllowed,
  forwardToGithub,
  type GitRequest,
} from '@/lib/bridge/git-proxy'
import { _resetForTests, getStore } from '@/lib/bridge/db'
import { setSessionContainer } from '@/lib/bridge/store'
import { getGithubToken } from '@/lib/connectors/github'

// getGithubToken is host-side; per-test we set its return. (Hoisted by vitest.)
vi.mock('@/lib/connectors/github', () => ({ getGithubToken: vi.fn() }))

describe('parseGitRequest', () => {
  test('info/refs fetch (v1+v2 share the path)', () => {
    const r = parseGitRequest(['owner', 'demo.git', 'info', 'refs'], new URLSearchParams('service=git-upload-pack'))
    expect(r).toEqual({ owner: 'owner', repo: 'demo', service: 'git-upload-pack', kind: 'info-refs' })
  })
  test('info/refs push', () => {
    const r = parseGitRequest(['owner', 'demo.git', 'info', 'refs'], new URLSearchParams('service=git-receive-pack'))
    expect(r?.service).toBe('git-receive-pack')
    expect(r?.kind).toBe('info-refs')
  })
  test('service POST endpoints', () => {
    expect(parseGitRequest(['o', 'r.git', 'git-upload-pack'], new URLSearchParams())?.kind).toBe('service')
    expect(parseGitRequest(['o', 'r', 'git-receive-pack'], new URLSearchParams())?.service).toBe('git-receive-pack')
  })
  test('rejects junk / unknown service / traversal', () => {
    expect(parseGitRequest(['owner', 'demo.git', 'info', 'refs'], new URLSearchParams('service=evil'))).toBeNull()
    expect(parseGitRequest(['owner', 'demo.git', 'HEAD'], new URLSearchParams())).toBeNull()
    expect(parseGitRequest(['..', 'demo.git', 'git-upload-pack'], new URLSearchParams())).toBeNull()
    expect(parseGitRequest(['owner'], new URLSearchParams())).toBeNull()
  })
})

describe('assertRepoAllowed', () => {
  test('membership is case-insensitive and .git-insensitive', () => {
    expect(assertRepoAllowed(['Owner/Demo'], 'owner', 'demo')).toBe(true)
    expect(assertRepoAllowed(['owner/demo.git'], 'owner', 'demo')).toBe(true)
    expect(assertRepoAllowed(['owner/demo'], 'owner', 'other')).toBe(false)
    expect(assertRepoAllowed([], 'owner', 'demo')).toBe(false)
  })
})

describe('forwardToGithub', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('PACK', { status: 200, headers: { 'content-type': 'application/x-git-upload-pack-result' } })),
    )
  })
  afterEach(() => vi.unstubAllGlobals())

  test('injects Basic x-access-token PAT, forwards git headers, builds upstream URL', async () => {
    const target: GitRequest = { owner: 'owner', repo: 'demo', service: 'git-upload-pack', kind: 'info-refs' }
    const req = new Request('http://host/whatever', {
      method: 'GET',
      headers: { 'git-protocol': 'version=2', accept: 'application/x-git-upload-pack-advertisement' },
    })
    const res = await forwardToGithub(req, target, 'PAT123')
    expect(res.status).toBe(200)
    expect(res.headers.get('content-type')).toBe('application/x-git-upload-pack-result')
    const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(String(url)).toBe('https://github.com/owner/demo.git/info/refs?service=git-upload-pack')
    const h = init.headers as Headers
    expect(h.get('authorization')).toBe('Basic ' + Buffer.from('x-access-token:PAT123').toString('base64'))
    expect(h.get('git-protocol')).toBe('version=2')
    expect(h.get('accept')).toBe('application/x-git-upload-pack-advertisement')
  })

  test('service POST targets the named endpoint and streams the body', async () => {
    const target: GitRequest = { owner: 'o', repo: 'r', service: 'git-receive-pack', kind: 'service' }
    const req = new Request('http://host/x', {
      method: 'POST',
      body: 'PUSHDATA',
      headers: { 'content-type': 'application/x-git-receive-pack-request' },
    })
    await forwardToGithub(req, target, 'P')
    const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(String(url)).toBe('https://github.com/o/r.git/git-receive-pack')
    expect(init.method).toBe('POST')
    expect(init.duplex).toBe('half')
  })
})

describe('git proxy route', () => {
  function seed(scopeRepo = 'owner/demo', cap = 'git_cap') {
    const store = getStore()
    store.db
      .prepare('INSERT INTO sessions (session_id, environment_id, archived, created_at, worker_epoch) VALUES (?, NULL, 0, ?, 0)')
      .run('sess1', Date.now())
    setSessionContainer(store, 'sess1', { container: 'c', repo: scopeRepo, gitCapToken: cap })
  }
  function basic(cap: string) {
    return 'Basic ' + Buffer.from(`x-access-token:${cap}`).toString('base64')
  }
  function ctx(path: string[]) {
    return { params: Promise.resolve({ sessionId: 'sess1', path }) }
  }
  async function route() {
    // A variable specifier so vite doesn't glob-scan the literal [...path]
    // catch-all brackets (a static string here fails module resolution).
    const mod = '@/app/api/bridge/v1/code/sessions/[sessionId]/git/[...path]/route'
    return import(/* @vite-ignore */ mod)
  }

  beforeEach(() => {
    _resetForTests()
    vi.mocked(getGithubToken).mockResolvedValue('PAT')
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('OK', { status: 200, headers: { 'content-type': 'application/x-git-upload-pack-advertisement' } })),
    )
  })
  afterEach(() => vi.unstubAllGlobals())

  test('401 on bad cap token', async () => {
    seed()
    const { GET } = await route()
    const req = new Request('http://h/info/refs?service=git-upload-pack', { headers: { authorization: basic('wrong') } })
    const res = await GET(req, ctx(['owner', 'demo.git', 'info', 'refs']))
    expect(res.status).toBe(401)
  })

  test('403 + audit event on out-of-scope repo', async () => {
    seed('owner/demo', 'git_cap')
    const { GET } = await route()
    const req = new Request('http://h/info/refs?service=git-upload-pack', { headers: { authorization: basic('git_cap') } })
    const res = await GET(req, ctx(['evil', 'other.git', 'info', 'refs']))
    expect(res.status).toBe(403)
    const rows = getStore().db
      .prepare('SELECT payload_json FROM session_events WHERE session_id = ?')
      .all('sess1') as Array<{ payload_json: string }>
    expect(rows.some((r) => /blocked out-of-scope/.test(r.payload_json))).toBe(true)
  })

  test('happy path forwards + streams 200', async () => {
    seed('owner/demo', 'git_cap')
    const { GET } = await route()
    const req = new Request('http://h/info/refs?service=git-upload-pack', { headers: { authorization: basic('git_cap') } })
    const res = await GET(req, ctx(['owner', 'demo.git', 'info', 'refs']))
    expect(res.status).toBe(200)
    expect(res.headers.get('content-type')).toContain('x-git-upload-pack-advertisement')
    const [url, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(String(url)).toContain('github.com/owner/demo.git/info/refs')
    expect((init.headers as Headers).get('authorization')).toContain('Basic ')
  })

  test('503 when no PAT host-side', async () => {
    seed('owner/demo', 'git_cap')
    vi.mocked(getGithubToken).mockResolvedValue(null)
    const { GET } = await route()
    const req = new Request('http://h/info/refs?service=git-upload-pack', { headers: { authorization: basic('git_cap') } })
    const res = await GET(req, ctx(['owner', 'demo.git', 'info', 'refs']))
    expect(res.status).toBe(503)
  })
})
