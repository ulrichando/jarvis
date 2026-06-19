import { describe, expect, test, beforeEach, afterEach, vi } from 'vitest'
import {
  parseGitRequest,
  assertRepoAllowed,
  forwardToGithub,
  type GitRequest,
} from '@/lib/bridge/git-proxy'

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
