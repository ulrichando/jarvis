import { afterEach, describe, expect, test } from 'bun:test'
import {
  parseDuckDuckGoHtml,
  searchDuckDuckGo,
  searchSearxng,
  webSearch,
} from './webSearch.js'

const origFetch = globalThis.fetch
afterEach(() => {
  globalThis.fetch = origFetch
  delete process.env.SEARXNG_URL
})

function mockFetch(
  handler: (url: string) => {
    ok: boolean
    status?: number
    json?: unknown
    text?: string
  },
): void {
  globalThis.fetch = (async (url: unknown) => {
    const r = handler(String(url))
    return {
      ok: r.ok,
      status: r.status ?? (r.ok ? 200 : 500),
      json: async () => r.json,
      text: async () => r.text ?? '',
    } as unknown as Response
  }) as typeof fetch
}

const DDG_ANCHOR = '<a class="result__a" href="https://ddg-result.com">DDG hit</a>'

describe('searchSearxng', () => {
  test('parses results[].{title,url} and drops incomplete rows', async () => {
    process.env.SEARXNG_URL = 'http://127.0.0.1:8888'
    mockFetch(() => ({
      ok: true,
      json: {
        results: [
          { title: 'Alpha', url: 'https://alpha.com', score: 2 },
          { title: '', url: 'https://no-title.com' },
          { title: 'Beta', url: '' },
        ],
      },
    }))
    expect(await searchSearxng('q')).toEqual([{ title: 'Alpha', url: 'https://alpha.com' }])
  })
  test('throws when SEARXNG_URL is unset', async () => {
    delete process.env.SEARXNG_URL
    await expect(searchSearxng('q')).rejects.toThrow(/SEARXNG_URL/)
  })
  test('throws on a non-200 (e.g. 403 format-not-enabled)', async () => {
    process.env.SEARXNG_URL = 'http://x'
    mockFetch(() => ({ ok: false, status: 403 }))
    await expect(searchSearxng('q')).rejects.toThrow(/403/)
  })
})

describe('searchDuckDuckGo — CAPTCHA is a failure, not a silent empty', () => {
  test('throws on an anti-bot page that has zero results', async () => {
    mockFetch(() => ({ ok: true, text: '<html><body>Please complete the CAPTCHA</body></html>' }))
    await expect(searchDuckDuckGo('q')).rejects.toThrow(/blocked|captcha|anti-bot/i)
  })
  test('a genuine no-results page (no block markers) returns []', async () => {
    mockFetch(() => ({ ok: true, text: '<html><body>No results.</body></html>' }))
    expect(await searchDuckDuckGo('q')).toEqual([])
  })
})

describe('webSearch — SearXNG preferred, DuckDuckGo fallback', () => {
  test('prefers SearXNG when SEARXNG_URL is set', async () => {
    process.env.SEARXNG_URL = 'http://searx.local'
    let hitSearx = false
    mockFetch((url) => {
      if (url.includes('searx.local')) {
        hitSearx = true
        return { ok: true, json: { results: [{ title: 'S', url: 'https://s.com' }] } }
      }
      return { ok: true, text: DDG_ANCHOR }
    })
    const hits = await webSearch('q')
    expect(hitSearx).toBe(true)
    expect(hits).toEqual([{ title: 'S', url: 'https://s.com' }])
  })
  test('falls back to DuckDuckGo when SearXNG errors', async () => {
    process.env.SEARXNG_URL = 'http://searx.local'
    mockFetch((url) =>
      url.includes('searx.local')
        ? { ok: false, status: 500 }
        : { ok: true, text: DDG_ANCHOR },
    )
    const hits = await webSearch('q')
    expect(hits[0]?.url).toContain('ddg-result.com')
  })
  test('uses DuckDuckGo directly when SEARXNG_URL is unset', async () => {
    delete process.env.SEARXNG_URL
    let hitDdg = false
    mockFetch((url) => {
      if (url.includes('duckduckgo')) hitDdg = true
      return { ok: true, text: DDG_ANCHOR }
    })
    await webSearch('q')
    expect(hitDdg).toBe(true)
  })
})

describe('parseDuckDuckGoHtml (regression guard)', () => {
  test('extracts title + unwrapped url', () => {
    expect(parseDuckDuckGoHtml(DDG_ANCHOR)).toEqual([
      { title: 'DDG hit', url: 'https://ddg-result.com/' },
    ])
  })
})
