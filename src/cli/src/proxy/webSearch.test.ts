import { afterEach, describe, expect, test } from 'bun:test'
import {
  buildSyntheticWebSearchResponse,
  parseDuckDuckGoHtml,
  searchDuckDuckGo,
  searchSearxng,
  webSearch,
} from './webSearch.js'

const origFetch = globalThis.fetch
afterEach(() => {
  globalThis.fetch = origFetch
  delete process.env.SEARXNG_URL
  delete process.env.BRAVE_SEARCH_API_KEY
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
      headers: new Headers(),
      json: async () => r.json,
      text: async () => r.text ?? '',
    } as unknown as Response
  }) as typeof fetch
}

const DDG_ANCHOR = '<a class="result__a" href="https://ddg-result.com">DDG hit</a>'

describe('searchSearxng', () => {
  test('parses results[].{title,url,content} and drops incomplete rows', async () => {
    process.env.SEARXNG_URL = 'http://127.0.0.1:8888'
    mockFetch(() => ({
      ok: true,
      json: {
        results: [
          { title: 'Alpha', url: 'https://alpha.com', content: 'about alpha', score: 2 },
          { title: '', url: 'https://no-title.com' },
          { title: 'Beta', url: '' },
        ],
      },
    }))
    expect(await searchSearxng('q')).toEqual([
      { title: 'Alpha', url: 'https://alpha.com', snippet: 'about alpha' },
    ])
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

describe('webSearch — keyless chain: SearXNG preferred, DuckDuckGo fallback', () => {
  test('prefers SearXNG when SEARXNG_URL is set and no Brave key exists', async () => {
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
    expect(hits).toEqual([{ title: 'S', url: 'https://s.com', snippet: '' }])
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
  test('never touches the Brave endpoint without BRAVE_SEARCH_API_KEY', async () => {
    delete process.env.BRAVE_SEARCH_API_KEY
    process.env.SEARXNG_URL = 'http://searx.local'
    let hitBrave = false
    mockFetch((url) => {
      if (url.includes('api.search.brave.com')) hitBrave = true
      return { ok: true, json: { results: [{ title: 'S', url: 'https://s.com/x' }] } }
    })
    await webSearch('q')
    expect(hitBrave).toBe(false)
  })
})

describe('webSearch — Brave primary when keyed', () => {
  test('uses Brave first and keeps its ranking', async () => {
    process.env.BRAVE_SEARCH_API_KEY = 'test-key'
    process.env.SEARXNG_URL = 'http://searx.local'
    mockFetch((url) => {
      if (url.includes('api.search.brave.com')) {
        return {
          ok: true,
          json: {
            web: {
              results: [
                { title: 'First', url: 'https://one.com/a', description: 'd1' },
                { title: 'Second', url: 'https://two.com/b', description: 'd2' },
              ],
            },
          },
        }
      }
      throw new Error('should not reach fallback')
    })
    const hits = await webSearch('anything at all')
    expect(hits.map(h => h.title)).toEqual(['First', 'Second'])
  })
  test('falls back to SearXNG when Brave errors', async () => {
    process.env.BRAVE_SEARCH_API_KEY = 'test-key'
    process.env.SEARXNG_URL = 'http://searx.local'
    mockFetch((url) => {
      if (url.includes('api.search.brave.com')) return { ok: false, status: 500 }
      if (url.includes('searx.local')) {
        return { ok: true, json: { results: [{ title: 'Fallback', url: 'https://f.com/x' }] } }
      }
      return { ok: true, text: '' }
    })
    const hits = await webSearch('q')
    expect(hits[0]?.title).toBe('Fallback')
  })
})

describe('parseDuckDuckGoHtml (regression guard)', () => {
  test('extracts title + unwrapped url', () => {
    expect(parseDuckDuckGoHtml(DDG_ANCHOR)).toEqual([
      { title: 'DDG hit', url: 'https://ddg-result.com/', snippet: '' },
    ])
  })
})

describe('buildSyntheticWebSearchResponse — enriched synthetic message', () => {
  const hits = [
    { title: 'A', url: 'https://a.com/x', snippet: 'about a', content: 'Full text of A.' },
    { title: 'B', url: 'https://b.com/y', snippet: '' },
  ]
  test('keeps the spec {title,url} result items the phone parses', () => {
    const msg = buildSyntheticWebSearchResponse('q', 'm', hits, false) as any
    const toolResult = msg.content.find((b: any) => b.type === 'web_search_tool_result')
    expect(toolResult.content.map((r: any) => ({ title: r.title, url: r.url }))).toEqual([
      { title: 'A', url: 'https://a.com/x' },
      { title: 'B', url: 'https://b.com/y' },
    ])
  })
  test('appends a text digest block carrying real page content', () => {
    const msg = buildSyntheticWebSearchResponse('q', 'm', hits, false) as any
    const text = msg.content.find((b: any) => b.type === 'text')
    expect(text.text).toContain('Full text of A.')
    expect(text.text).toContain('https://a.com/x')
  })
  test('failed search yields the error shape and no text block', () => {
    const msg = buildSyntheticWebSearchResponse('q', 'm', [], true) as any
    const toolResult = msg.content.find((b: any) => b.type === 'web_search_tool_result')
    expect(toolResult.content.type).toBe('web_search_tool_result_error')
    expect(msg.content.some((b: any) => b.type === 'text')).toBe(false)
  })
})

describe('enriched result items — the source-card contract', () => {
  // The documented shape the Android parser consumes:
  //   { type, title, url, snippet?, cited_text?, encrypted_content, page_age }
  const hits = [
    {
      title: 'Launch report',
      url: 'https://news.com/launch',
      snippet: 'Rocket set for launch',
      content:
        'Filler intro line for the page, quite long and not that useful here.\n' +
        'The launch happens on March 12 according to agency officials, weather permitting.\n' +
        'Closing boilerplate for the page footer area.',
    },
    { title: 'Thin hit', url: 'https://thin.com/x', snippet: '' },
  ]

  test('each item carries snippet + cited_text alongside the spec fields', () => {
    const msg = buildSyntheticWebSearchResponse('when is the launch', 'm', hits, false) as any
    const items = msg.content.find((b: any) => b.type === 'web_search_tool_result').content
    expect(items[0].type).toBe('web_search_result')
    expect(items[0].title).toBe('Launch report')
    expect(items[0].url).toBe('https://news.com/launch')
    expect(items[0].snippet).toBe('Rocket set for launch')
    // cited_text is the best supporting passage from the FETCHED content.
    expect(items[0].cited_text).toContain('March 12')
    expect(items[0].encrypted_content).toBe('')
    expect(items[0].page_age).toBeNull()
  })

  test('optional fields are omitted (not empty) when there is nothing to say', () => {
    const msg = buildSyntheticWebSearchResponse('q', 'm', hits, false) as any
    const thin = msg.content.find((b: any) => b.type === 'web_search_tool_result').content[1]
    expect('snippet' in thin).toBe(false)
    expect('cited_text' in thin).toBe(false)
    expect(thin.title).toBe('Thin hit')
  })

  test('digest text block carries the citation + abstention contract', () => {
    const msg = buildSyntheticWebSearchResponse('when is the launch', 'm', hits, false) as any
    const text = msg.content.find((b: any) => b.type === 'text')
    expect(text.text).toContain('Attribute each substantive claim')
    expect(text.text).toContain("couldn't find reliable sources")
  })
})
