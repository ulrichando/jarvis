import { describe, expect, test, beforeEach, afterEach, vi } from 'vitest'
import { promises as fs } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { openPullRequest, mergePullRequest, githubPrStatus } from '@/lib/connectors/github'

// Hermetic: point the connector at a temp file holding a connected token via
// JARVIS_CONNECTORS_FILE, instead of mocking node:fs (which doesn't reliably
// intercept `import { promises as fs } from "node:fs"`, so the old test only
// passed when a real ~/.jarvis/connectors.json happened to exist).
let tmpFile: string
beforeEach(async () => {
  tmpFile = path.join(os.tmpdir(), `connectors-${Date.now()}-${Math.random().toString(36).slice(2)}.json`)
  await fs.writeFile(tmpFile, JSON.stringify({ github: { token: 't', login: 'me', connectedAt: 1 } }))
  process.env.JARVIS_CONNECTORS_FILE = tmpFile
  vi.stubGlobal('fetch', vi.fn())
})
afterEach(async () => {
  delete process.env.JARVIS_CONNECTORS_FILE
  vi.unstubAllGlobals()
  await fs.rm(tmpFile, { force: true })
})

describe('openPullRequest', () => {
  test('POSTs /pulls and returns url+number', async () => {
    ;(fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      new Response(JSON.stringify({ html_url: 'https://gh/pr/1', number: 1 }), { status: 201 }),
    )
    const r = await openPullRequest('owner/demo', 'jarvis/x', 'main', 'T', 'B')
    expect(r).toEqual({ ok: true, url: 'https://gh/pr/1', number: 1 })
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(String(url)).toBe('https://api.github.com/repos/owner/demo/pulls')
    expect(init.method).toBe('POST')
  })

  test('422 (exists) → finds the open PR', async () => {
    ;(fetch as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce(new Response('dup', { status: 422 }))
      .mockResolvedValueOnce(new Response(JSON.stringify([{ html_url: 'https://gh/pr/9', number: 9 }]), { status: 200 }))
    const r = await openPullRequest('owner/demo', 'jarvis/x', 'main', 'T', 'B')
    expect(r).toEqual({ ok: true, url: 'https://gh/pr/9', number: 9 })
  })
})

describe('mergePullRequest', () => {
  test('PUTs /merge', async () => {
    ;(fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(new Response('{}', { status: 200 }))
    expect(await mergePullRequest('owner/demo', 9)).toEqual({ ok: true })
    const [url, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(String(url)).toBe('https://api.github.com/repos/owner/demo/pulls/9/merge')
    expect(init.method).toBe('PUT')
  })

  test('non-2xx → error', async () => {
    ;(fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(new Response('blocked', { status: 405 }))
    const r = await mergePullRequest('owner/demo', 9)
    expect(r.ok).toBe(false)
  })
})

// ── Injected-token path (external gh-app jobs): the optional `token` param
// authenticates INSTEAD of the stored connector PAT; the default (no token)
// path stays byte-identical — proven by the existing tests above, which pass
// no token and still authenticate via the connector file.
describe('optional token param (App installation token)', () => {
  test('openPullRequest authenticates with the passed token, not the stored PAT', async () => {
    ;(fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      new Response(JSON.stringify({ html_url: 'https://gh/pr/2', number: 2 }), { status: 201 }),
    )
    const r = await openPullRequest('owner/demo', 'jarvis/x', 'main', 'T', 'B', false, 'ghs_inst_tok')
    expect(r.ok).toBe(true)
    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer ghs_inst_tok')
  })

  test('openPullRequest with a token works with NO connector configured', async () => {
    // Point the connector store at a missing file — "GitHub not connected".
    process.env.JARVIS_CONNECTORS_FILE = path.join(os.tmpdir(), 'nope-does-not-exist.json')
    ;(fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      new Response(JSON.stringify({ html_url: 'https://gh/pr/3', number: 3 }), { status: 201 }),
    )
    const r = await openPullRequest('owner/demo', 'jarvis/x', 'main', 'T', 'B', false, 'ghs_inst_tok')
    expect(r).toEqual({ ok: true, url: 'https://gh/pr/3', number: 3 })
    // …while the SAME call without a token still fails closed (default path).
    const r2 = await openPullRequest('owner/demo', 'jarvis/x', 'main', 'T', 'B')
    expect(r2).toEqual({ ok: false, error: 'GitHub not connected' })
  })

  test('githubPrStatus authenticates with the passed token', async () => {
    ;(fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      new Response(JSON.stringify([]), { status: 200 }),
    )
    const r = await githubPrStatus('owner/demo', 'jarvis/x', 'ghs_inst_tok')
    expect(r.ok).toBe(true)
    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer ghs_inst_tok')
  })

  test('githubPrStatus without a token keeps the stored-PAT path', async () => {
    ;(fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      new Response(JSON.stringify([]), { status: 200 }),
    )
    const r = await githubPrStatus('owner/demo', 'jarvis/x')
    expect(r.ok).toBe(true)
    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer t')
  })
})

describe('input guards (request-forgery / SSRF)', () => {
  test('a repo with URL-control chars or traversal is rejected WITHOUT fetching', async () => {
    for (const bad of ['owner', 'owner/demo@evil.com', 'owner/demo?x=1', '../../etc', 'owner/de mo', 'a/b/c']) {
      const r = await mergePullRequest(bad, 9)
      expect(r.ok).toBe(false)
    }
    expect((fetch as ReturnType<typeof vi.fn>).mock.calls.length).toBe(0)
  })

  test('a non-integer PR number is rejected WITHOUT fetching', async () => {
    const r = await mergePullRequest('owner/demo', Number.NaN)
    expect(r.ok).toBe(false)
    expect((fetch as ReturnType<typeof vi.fn>).mock.calls.length).toBe(0)
  })

  test('a valid repo with a slashed branch %-encodes the head query param', async () => {
    ;(fetch as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce(new Response('dup', { status: 422 }))
      .mockResolvedValueOnce(new Response(JSON.stringify([{ html_url: 'u', number: 3 }]), { status: 200 }))
    await openPullRequest('owner/demo', 'feature/x', 'main', 'T', 'B')
    const secondUrl = String((fetch as ReturnType<typeof vi.fn>).mock.calls[1][0])
    expect(secondUrl).toContain('head=owner:feature%2Fx') // slash encoded, not a raw path break
  })
})
