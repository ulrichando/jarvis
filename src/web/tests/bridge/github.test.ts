import { describe, expect, test, beforeEach, afterEach, vi } from 'vitest'
import { openPullRequest, mergePullRequest } from '@/lib/connectors/github'

// Make load() return a connected token without touching the real
// ~/.jarvis/connectors.json (github.ts reads it via fs.promises.readFile).
vi.mock('node:fs', async (orig) => {
  const real = await orig<typeof import('node:fs')>()
  return {
    ...real,
    promises: {
      ...real.promises,
      readFile: async () => JSON.stringify({ github: { token: 't', login: 'me', connectedAt: 1 } }),
    },
  }
})

beforeEach(() => vi.stubGlobal('fetch', vi.fn()))
afterEach(() => vi.unstubAllGlobals())

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
