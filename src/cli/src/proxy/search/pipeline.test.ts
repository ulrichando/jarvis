import { afterEach, beforeEach, describe, expect, test } from 'bun:test'
import {
  __braveTuningForTests,
  enrichHitsWithContent,
  runSearchPipeline,
  searchBrave,
} from './pipeline'
import type { RichHit } from './core'

const origFetch = globalThis.fetch
const origTuning = { ...__braveTuningForTests }

beforeEach(() => {
  // Don't sleep real seconds in tests; production keeps 1050/1100ms.
  __braveTuningForTests.minIntervalMs = 1
  __braveTuningForTests.retryDelayMs = 1
  delete process.env.BRAVE_SEARCH_API_KEY
  delete process.env.SEARXNG_URL
})
afterEach(() => {
  globalThis.fetch = origFetch
  Object.assign(__braveTuningForTests, origTuning)
  delete process.env.BRAVE_SEARCH_API_KEY
  delete process.env.SEARXNG_URL
})

type MockResponse = {
  ok?: boolean
  status?: number
  headers?: Record<string, string>
  json?: unknown
  text?: string
}

function mockFetch(
  handler: (url: string, init?: RequestInit) => MockResponse | Promise<MockResponse>,
): string[] {
  const calls: string[] = []
  globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
    calls.push(String(url))
    const r = await handler(String(url), init)
    return {
      ok: r.ok ?? true,
      status: r.status ?? (r.ok === false ? 500 : 200),
      headers: new Headers(r.headers ?? {}),
      json: async () => r.json,
      text: async () => r.text ?? '',
    } as unknown as Response
  }) as typeof fetch
  return calls
}

const BRAVE_OK = {
  json: {
    web: {
      results: [
        { title: 'Result One', url: 'https://one.com/a', description: 'about one' },
        { title: 'Result Two', url: 'https://two.com/b', description: 'about two' },
      ],
    },
  },
}

describe('searchBrave', () => {
  test('sends the subscription token and parses web.results', async () => {
    process.env.BRAVE_SEARCH_API_KEY = 'k-123'
    let sawToken = ''
    let sawUrl = ''
    mockFetch((url, init) => {
      sawUrl = url
      sawToken = new Headers(init?.headers).get('X-Subscription-Token') ?? ''
      return BRAVE_OK
    })
    const hits = await searchBrave('ai agents')
    expect(sawToken).toBe('k-123')
    expect(sawUrl).toContain('api.search.brave.com/res/v1/web/search')
    expect(sawUrl).toContain('q=ai+agents')
    expect(sawUrl).toContain('extra_snippets=1')
    expect(hits).toHaveLength(2)
  })
  test('throws without a key', async () => {
    await expect(searchBrave('q')).rejects.toThrow(/BRAVE_SEARCH_API_KEY/)
  })
  test('retries once on 429 (free tier: 1 req/s)', async () => {
    process.env.BRAVE_SEARCH_API_KEY = 'k'
    let n = 0
    mockFetch(() => (++n === 1 ? { ok: false, status: 429 } : BRAVE_OK))
    const hits = await searchBrave('q')
    expect(n).toBe(2)
    expect(hits).toHaveLength(2)
  })
  test('drops extra_snippets and retries when the plan rejects it', async () => {
    process.env.BRAVE_SEARCH_API_KEY = 'k'
    const calls = mockFetch((url) =>
      url.includes('extra_snippets') ? { ok: false, status: 422 } : BRAVE_OK,
    )
    const hits = await searchBrave('q')
    expect(hits).toHaveLength(2)
    expect(calls[calls.length - 1]).not.toContain('extra_snippets')
  })
  test('gives up after the 429 retry fails', async () => {
    process.env.BRAVE_SEARCH_API_KEY = 'k'
    mockFetch(() => ({ ok: false, status: 429 }))
    await expect(searchBrave('q')).rejects.toThrow(/429/)
  })
})

describe('runSearchPipeline — source chain', () => {
  test('keyless: Brave endpoint is never called (today’s behavior intact)', async () => {
    process.env.SEARXNG_URL = 'http://searx.local'
    const calls = mockFetch((url) => {
      if (url.includes('searx.local')) {
        return { json: { results: [{ title: 'S', url: 'https://s.com/x', content: 'c' }] } }
      }
      return { text: '' }
    })
    const { hits, provider } = await runSearchPipeline('q')
    expect(provider).toBe('searxng')
    expect(hits[0]?.title).toBe('S')
    expect(calls.some(u => u.includes('api.search.brave.com'))).toBe(false)
  })
  test('keyed: Brave wins and fallbacks are not called', async () => {
    process.env.BRAVE_SEARCH_API_KEY = 'k'
    process.env.SEARXNG_URL = 'http://searx.local'
    const calls = mockFetch((url) => {
      if (url.includes('api.search.brave.com')) return BRAVE_OK
      throw new Error('fallback should not be reached')
    })
    const { provider } = await runSearchPipeline('q')
    expect(provider).toBe('brave')
    expect(calls).toHaveLength(1)
  })
  test('Brave error → SearXNG → DDG chain, in order', async () => {
    process.env.BRAVE_SEARCH_API_KEY = 'k'
    process.env.SEARXNG_URL = 'http://searx.local'
    const calls = mockFetch((url) => {
      if (url.includes('api.search.brave.com')) return { ok: false, status: 500 }
      if (url.includes('searx.local')) return { ok: false, status: 500 }
      return {
        text: '<a class="result__a" href="https://ddg.com/hit">DDG hit</a>',
      }
    })
    const { hits, provider } = await runSearchPipeline('q')
    expect(provider).toBe('duckduckgo')
    expect(hits[0]?.url).toBe('https://ddg.com/hit')
    expect(calls.filter(u => u.includes('api.search.brave.com'))).toHaveLength(1)
  })
  test('throws only when every backend errors', async () => {
    process.env.BRAVE_SEARCH_API_KEY = 'k'
    process.env.SEARXNG_URL = 'http://searx.local'
    mockFetch(() => ({ ok: false, status: 500 }))
    await expect(runSearchPipeline('q')).rejects.toThrow(/brave.*searxng.*duckduckgo/s)
  })
  test('a backend that finds nothing yields empty hits, not an error', async () => {
    process.env.SEARXNG_URL = 'http://searx.local'
    mockFetch((url) => {
      if (url.includes('searx.local')) return { json: { results: [] } }
      return { text: '<html><body>No results.</body></html>' }
    })
    const { hits } = await runSearchPipeline('q')
    expect(hits).toEqual([])
  })
  test('cleans the degraded-SearXNG garbage (homepages sink, article wins)', async () => {
    process.env.SEARXNG_URL = 'http://searx.local'
    mockFetch(() => ({
      json: {
        results: [
          { title: 'BBC News', url: 'https://www.bbc.com/', engine: 'bing' },
          { title: 'CNN', url: 'https://edition.cnn.com/', engine: 'bing' },
          {
            title: 'AI agents 2026 report',
            url: 'https://example.org/ai-agents-2026',
            content: 'the definitive ai agents 2026 report',
            engine: 'bing',
          },
        ],
      },
    }))
    const { hits } = await runSearchPipeline('latest developments in ai agents 2026')
    expect(hits.map(h => h.url)).toEqual(['https://example.org/ai-agents-2026'])
  })
})

describe('enrichHitsWithContent', () => {
  const page = (body: string) =>
    `<html><body><article><p>${body.repeat(30)}</p></article></body></html>`
  const hits: RichHit[] = [
    { title: 'A', url: 'https://a.com/x', snippet: 'sa' },
    { title: 'B', url: 'https://b.com/y', snippet: 'sb' },
    { title: 'C', url: 'https://c.com/z', snippet: 'sc' },
  ]

  test('fetches the top N pages in parallel and attaches extracted text', async () => {
    const calls = mockFetch((url) => ({
      headers: { 'content-type': 'text/html' },
      text: page(`Content of ${url} with enough words to pass the floor. `),
    }))
    const out = await enrichHitsWithContent(hits, { pages: 2, timeoutMs: 500, budgetMs: 1000 })
    expect(calls).toHaveLength(2)
    expect(out[0]?.content).toContain('Content of https://a.com/x')
    expect(out[1]?.content).toContain('Content of https://b.com/y')
    expect(out[2]?.content).toBeUndefined()
    // Never mutates the inputs.
    expect(hits[0]?.content).toBeUndefined()
  })
  test('pages: 0 disables fetching entirely', async () => {
    const calls = mockFetch(() => ({ text: page('x') }))
    const out = await enrichHitsWithContent(hits, { pages: 0 })
    expect(calls).toHaveLength(0)
    expect(out).toEqual(hits)
  })
  test('a failing or non-html page degrades to the snippet, not an error', async () => {
    mockFetch((url) => {
      if (url.includes('a.com')) return { ok: false, status: 500 }
      if (url.includes('b.com')) {
        return { headers: { 'content-type': 'application/pdf' }, text: '%PDF-1.4' }
      }
      return { headers: { 'content-type': 'text/html' }, text: page('Real long body text here. ') }
    })
    const out = await enrichHitsWithContent(hits, { pages: 3, timeoutMs: 500, budgetMs: 1000 })
    expect(out[0]?.content).toBeUndefined()
    expect(out[1]?.content).toBeUndefined()
    expect(out[2]?.content).toContain('Real long body text')
  })
  test('a hung page is cut by the per-page timeout without stalling the rest', async () => {
    mockFetch(async (url, init) => {
      if (url.includes('a.com')) {
        // Never resolves on its own — must be aborted by the signal.
        await new Promise<void>((resolve, reject) => {
          init?.signal?.addEventListener('abort', () => reject(new Error('aborted')))
        })
      }
      return { headers: { 'content-type': 'text/html' }, text: page('Fast page body text. ') }
    })
    const t0 = Date.now()
    const out = await enrichHitsWithContent(hits, { pages: 2, timeoutMs: 150, budgetMs: 5000 })
    expect(Date.now() - t0).toBeLessThan(2000)
    expect(out[0]?.content).toBeUndefined()
    expect(out[1]?.content).toContain('Fast page body')
  })
  test('never fetches private/in-network urls', async () => {
    const calls = mockFetch(() => ({ headers: { 'content-type': 'text/html' }, text: page('x ') }))
    await enrichHitsWithContent(
      [{ title: 'evil', url: 'http://searxng:8080/steal', snippet: '' }],
      { pages: 1, timeoutMs: 200, budgetMs: 500 },
    )
    expect(calls).toHaveLength(0)
  })
  test('short extractions (JS shells) are discarded', async () => {
    mockFetch(() => ({
      headers: { 'content-type': 'text/html' },
      text: '<html><body><div id="root"></div></body></html>',
    }))
    const out = await enrichHitsWithContent(hits, { pages: 1, timeoutMs: 200, budgetMs: 500 })
    expect(out[0]?.content).toBeUndefined()
  })
})
