import { afterEach, beforeEach, describe, expect, test } from 'bun:test'
import {
  __braveTuningForTests,
  __resetRerankThrottleForTests,
  enrichHitsWithContent,
  expandQueries,
  rerankHits,
  resolveRerankProvider,
  runResearchPipeline,
  runSearchPipeline,
  searchBrave,
} from './pipeline'
import type { RichHit } from './core'

const origFetch = globalThis.fetch
const origTuning = { ...__braveTuningForTests }

const SEARCH_ENV = [
  'BRAVE_SEARCH_API_KEY',
  'SEARXNG_URL',
  'COHERE_API_KEY',
  'JINA_API_KEY',
  'RERANK_PROVIDER',
  'RERANK_MODEL',
  'RERANK_TOP_N',
  'RERANK_TIMEOUT_MS',
  'RERANK_MAX_PER_MINUTE',
  'SEARCH_QUERY_REWRITE',
  'SEARCH_REWRITE_MAX_QUERIES',
  'SEARCH_REWRITE_TIMEOUT_MS',
]

beforeEach(() => {
  // Don't sleep real seconds in tests; production keeps 1050/1100ms.
  __braveTuningForTests.minIntervalMs = 1
  __braveTuningForTests.retryDelayMs = 1
  __resetRerankThrottleForTests()
  for (const k of SEARCH_ENV) delete process.env[k]
})
afterEach(() => {
  globalThis.fetch = origFetch
  Object.assign(__braveTuningForTests, origTuning)
  for (const k of SEARCH_ENV) delete process.env[k]
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

describe('resolveRerankProvider — key/env selection', () => {
  test('no keys → null (heuristic rerank keeps working)', () => {
    expect(resolveRerankProvider()).toBeNull()
  })
  test('auto picks Cohere first, then Jina', () => {
    process.env.COHERE_API_KEY = 'ck'
    process.env.JINA_API_KEY = 'jk'
    expect(resolveRerankProvider()).toEqual({ name: 'cohere', key: 'ck' })
    delete process.env.COHERE_API_KEY
    expect(resolveRerankProvider()).toEqual({ name: 'jina', key: 'jk' })
  })
  test('explicit RERANK_PROVIDER wins over auto order', () => {
    process.env.COHERE_API_KEY = 'ck'
    process.env.JINA_API_KEY = 'jk'
    process.env.RERANK_PROVIDER = 'jina'
    expect(resolveRerankProvider()).toEqual({ name: 'jina', key: 'jk' })
  })
  test('an explicit provider without its key → null (no cross-provider surprise)', () => {
    process.env.JINA_API_KEY = 'jk'
    process.env.RERANK_PROVIDER = 'cohere'
    expect(resolveRerankProvider()).toBeNull()
  })
  test('RERANK_PROVIDER=off disables even with keys', () => {
    process.env.COHERE_API_KEY = 'ck'
    process.env.RERANK_PROVIDER = 'off'
    expect(resolveRerankProvider()).toBeNull()
  })
})

describe('rerankHits — semantic rerank with heuristic-order floor', () => {
  const hits: RichHit[] = [
    { title: 'A', url: 'https://a.com/x', snippet: 'about a' },
    { title: 'B', url: 'https://b.com/y', snippet: 'about b', content: 'B page body text' },
    { title: 'C', url: 'https://c.com/z', snippet: 'about c' },
  ]

  test('no key → no network call, order unchanged', async () => {
    const calls = mockFetch(() => ({ json: {} }))
    const out = await rerankHits('q', hits)
    expect(calls).toHaveLength(0)
    expect(out).toEqual(hits)
  })

  test('Cohere: sends the documented v2 body and reorders by relevance_score', async () => {
    process.env.COHERE_API_KEY = 'ck'
    let sawUrl = ''
    let sawAuth = ''
    let sawBody: any = null
    mockFetch((url, init) => {
      sawUrl = url
      sawAuth = new Headers(init?.headers).get('Authorization') ?? ''
      sawBody = JSON.parse(String(init?.body))
      return {
        json: {
          results: [
            { index: 2, relevance_score: 0.98 },
            { index: 0, relevance_score: 0.41 },
            { index: 1, relevance_score: 0.07 },
          ],
        },
      }
    })
    const out = await rerankHits('which one is c', hits)
    expect(sawUrl).toBe('https://api.cohere.com/v2/rerank')
    expect(sawAuth).toBe('Bearer ck')
    expect(sawBody.model).toBe('rerank-v3.5')
    expect(sawBody.query).toBe('which one is c')
    expect(sawBody.documents).toHaveLength(3)
    expect(sawBody.documents[1]).toContain('B page body text') // content beats snippet
    expect(sawBody.top_n).toBe(3)
    expect(out.map(h => h.title)).toEqual(['C', 'A', 'B'])
    expect(out[0]?.rerankScore).toBe(0.98)
  })

  test('Jina: hits the jina endpoint with return_documents:false', async () => {
    process.env.JINA_API_KEY = 'jk'
    let sawUrl = ''
    let sawBody: any = null
    mockFetch((url, init) => {
      sawUrl = url
      sawBody = JSON.parse(String(init?.body))
      return { json: { results: [{ index: 1, relevance_score: 0.9 }] } }
    })
    const out = await rerankHits('q', hits)
    expect(sawUrl).toBe('https://api.jina.ai/v1/rerank')
    expect(sawBody.model).toBe('jina-reranker-v2-base-multilingual')
    expect(sawBody.return_documents).toBe(false)
    // Scored hit first; unscored keep their relative order after it.
    expect(out.map(h => h.title)).toEqual(['B', 'A', 'C'])
  })

  test('HTTP error → original order (graceful fallback)', async () => {
    process.env.COHERE_API_KEY = 'ck'
    mockFetch(() => ({ ok: false, status: 429 }))
    const out = await rerankHits('q', hits)
    expect(out).toEqual(hits)
  })

  test('junk body (no results) → original order', async () => {
    process.env.COHERE_API_KEY = 'ck'
    mockFetch(() => ({ json: { results: [{ index: 99 }, { index: -1 }] } }))
    const out = await rerankHits('q', hits)
    expect(out).toEqual(hits)
  })

  test('a hung API is cut by the timeout, order unchanged', async () => {
    process.env.COHERE_API_KEY = 'ck'
    mockFetch(async (_url, init) => {
      await new Promise<void>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => reject(new Error('aborted')))
      })
      return { json: {} }
    })
    const t0 = Date.now()
    const out = await rerankHits('q', hits, { timeoutMs: 100 })
    expect(Date.now() - t0).toBeLessThan(1500)
    expect(out).toEqual(hits)
  })

  test('client throttle: over RERANK_MAX_PER_MINUTE calls are skipped', async () => {
    process.env.COHERE_API_KEY = 'ck'
    process.env.RERANK_MAX_PER_MINUTE = '1'
    const calls = mockFetch(() => ({
      json: { results: [{ index: 0, relevance_score: 1 }] },
    }))
    await rerankHits('q', hits)
    const out2 = await rerankHits('q', hits)
    expect(calls).toHaveLength(1) // second call never hit the network
    expect(out2).toEqual(hits)
  })

  test('single hit → no call (nothing to reorder)', async () => {
    process.env.COHERE_API_KEY = 'ck'
    const calls = mockFetch(() => ({ json: {} }))
    await rerankHits('q', [hits[0]])
    expect(calls).toHaveLength(0)
  })
})

describe('expandQueries — stage 1 orchestration', () => {
  test('mode off → the untouched original, always', async () => {
    expect(
      await expandQueries('hey jarvis what is x? and what is y', { mode: 'off' }),
    ).toEqual(['hey jarvis what is x? and what is y'])
  })
  test('SEARCH_QUERY_REWRITE=off honored from env', async () => {
    process.env.SEARCH_QUERY_REWRITE = 'off'
    expect(await expandQueries('hey jarvis tell me about rust')).toEqual([
      'hey jarvis tell me about rust',
    ])
  })
  test('default heuristic: simple query stays single', async () => {
    expect(await expandQueries('rust async traits 2026')).toEqual([
      'rust async traits 2026',
    ])
  })
  test('llm mode uses the injected rewriter', async () => {
    const out = await expandQueries('what is x and how does y work', {
      mode: 'llm',
      llmRewrite: async () => '["x definition", "y mechanism"]',
    })
    expect(out).toEqual(['x definition', 'y mechanism'])
  })
  test('llm failure → heuristic queries (never lost)', async () => {
    const out = await expandQueries('who won the final? what was the score', {
      mode: 'llm',
      llmRewrite: async () => {
        throw new Error('upstream down')
      },
    })
    expect(out).toEqual(['who won the final?', 'what was the score'])
  })
  test('a slow llm rewrite is timed out and degrades to heuristic', async () => {
    const t0 = Date.now()
    const out = await expandQueries('simple question here', {
      mode: 'llm',
      timeoutMs: 50,
      llmRewrite: () => new Promise(resolve => setTimeout(() => resolve('["late"]'), 5000)),
    })
    expect(Date.now() - t0).toBeLessThan(1000)
    expect(out).toEqual(['simple question here'])
  })
  test('llm mode without an injected fn → heuristic', async () => {
    process.env.SEARCH_QUERY_REWRITE = 'llm'
    expect(await expandQueries('plain query text')).toEqual(['plain query text'])
  })
})

describe('runResearchPipeline — full pass + no-key regression proofs', () => {
  const searxHits = (...titles: string[]) => ({
    json: {
      results: titles.map((t, i) => ({
        title: t,
        url: `https://${t.toLowerCase().replace(/\s+/g, '')}.com/p${i}`,
        content: `${t} body`,
      })),
    },
  })

  test('keyless simple query: exactly one search, no rerank/rewrite calls (today’s behavior)', async () => {
    process.env.SEARXNG_URL = 'http://searx.local'
    const calls = mockFetch(url => {
      if (url.includes('searx.local')) return searxHits('Alpha result')
      return { text: '' }
    })
    const { hits, provider, queries } = await runResearchPipeline('alpha result', {
      enrich: false,
    })
    expect(provider).toBe('searxng')
    expect(queries).toEqual(['alpha result'])
    expect(hits[0]?.title).toBe('Alpha result')
    // ONLY the one search request — no rerank endpoint, no rewrite call.
    expect(calls).toHaveLength(1)
    expect(calls[0]).toContain('searx.local')
  })

  test('rewrite off → the search runs the EXACT raw query', async () => {
    process.env.SEARXNG_URL = 'http://searx.local'
    const calls = mockFetch(() => searxHits('R'))
    await runResearchPipeline('hey jarvis please look up cats', {
      rewrite: { mode: 'off' },
      enrich: false,
    })
    expect(decodeURIComponent(calls[0])).toContain('hey jarvis please look up cats')
  })

  test('compound question → two searches, merged + domain-deduped', async () => {
    process.env.SEARXNG_URL = 'http://searx.local'
    const calls = mockFetch(url => {
      if (decodeURIComponent(url).includes('capital of australia')) {
        return searxHits('Canberra guide', 'Shared site')
      }
      return searxHits('Population stats', 'Shared site')
    })
    const { hits, queries } = await runResearchPipeline(
      'what is the capital of australia and how many people live there',
      { enrich: false },
    )
    expect(queries).toEqual([
      'what is the capital of australia',
      'how many people live there',
    ])
    expect(calls.filter(u => u.includes('searx.local'))).toHaveLength(2)
    const titles = hits.map(h => h.title)
    expect(titles).toContain('Canberra guide')
    expect(titles).toContain('Population stats')
    expect(titles.filter(t => t === 'Shared site')).toHaveLength(1) // deduped
  })

  test('one sub-query failing does not sink the other', async () => {
    process.env.SEARXNG_URL = 'http://searx.local'
    mockFetch(url => {
      const q = decodeURIComponent(url)
      if (q.includes('searx.local') && q.includes('capital of australia')) {
        return searxHits('Canberra guide')
      }
      return { ok: false, status: 500 }
    })
    const { hits } = await runResearchPipeline(
      'what is the capital of australia and how many people live there',
      { enrich: false },
    )
    expect(hits.map(h => h.title)).toContain('Canberra guide')
  })

  test('with a Cohere key the final order is the reranked one', async () => {
    process.env.SEARXNG_URL = 'http://searx.local'
    process.env.COHERE_API_KEY = 'ck'
    mockFetch((url, init) => {
      if (url.includes('searx.local')) {
        return searxHits('Alpha piece', 'Beta piece')
      }
      if (url.includes('api.cohere.com')) {
        // Score by document CONTENT (the pre-rerank heuristic order is an
        // implementation detail): Beta is the semantically relevant one.
        const body = JSON.parse(String(init?.body))
        const beta = body.documents.findIndex((d: string) => d.includes('Beta piece'))
        const alpha = body.documents.findIndex((d: string) => d.includes('Alpha piece'))
        return {
          json: {
            results: [
              { index: beta, relevance_score: 0.95 },
              { index: alpha, relevance_score: 0.2 },
            ],
          },
        }
      }
      return { text: '' }
    })
    const { hits } = await runResearchPipeline('some query words', { enrich: false })
    expect(hits.map(h => h.title)).toEqual(['Beta piece', 'Alpha piece'])
    expect(hits[0]?.rerankScore).toBe(0.95)
    expect(hits[1]?.rerankScore).toBe(0.2)
  })

  test('rerank API failure keeps the heuristic order (no regression)', async () => {
    process.env.SEARXNG_URL = 'http://searx.local'
    process.env.COHERE_API_KEY = 'ck'
    mockFetch(url => {
      if (url.includes('searx.local')) return searxHits('Kept first', 'Kept second')
      if (url.includes('api.cohere.com')) return { ok: false, status: 500 }
      return { text: '' }
    })
    const { hits } = await runResearchPipeline('kept query', { enrich: false })
    expect(hits.map(h => h.title)).toEqual(['Kept first', 'Kept second'])
  })

  test('rerank: false never touches the rerank endpoint even with a key', async () => {
    process.env.SEARXNG_URL = 'http://searx.local'
    process.env.COHERE_API_KEY = 'ck'
    const calls = mockFetch(url => {
      if (url.includes('searx.local')) return searxHits('Only hit here')
      return { text: '' }
    })
    await runResearchPipeline('only hit', { enrich: false, rerank: false })
    expect(calls.some(u => u.includes('cohere'))).toBe(false)
  })

  test('every backend failing still throws (semantics preserved)', async () => {
    process.env.SEARXNG_URL = 'http://searx.local'
    mockFetch(() => ({ ok: false, status: 500 }))
    await expect(
      runResearchPipeline('any query at all', { enrich: false }),
    ).rejects.toThrow()
  })
})
