import { describe, expect, test } from 'bun:test'
import {
  bestSnippet,
  cleanResults,
  extractMainText,
  formatHitsAsDigest,
  isBareHomepage,
  isJunkHit,
  isNavigationalMatch,
  isPubliclyRoutableUrl,
  normalizeUrlKey,
  parseBraveWebResponse,
  parseDuckDuckGoHtml,
  parseSearxngResponse,
  registrableDomain,
  scoreHit,
  tokenize,
  truncateAtWord,
  type RichHit,
} from './core'

const hit = (over: Partial<RichHit> = {}): RichHit => ({
  title: 'T',
  url: 'https://example.com/page',
  snippet: '',
  ...over,
})

describe('parseBraveWebResponse', () => {
  test('maps web.results and strips description markup', () => {
    const hits = parseBraveWebResponse({
      web: {
        results: [
          {
            title: 'AI &amp; Agents',
            url: 'https://a.com/agents',
            description: 'The <strong>agents</strong> report',
            extra_snippets: ['First extra.', '<em>Second</em> extra.'],
          },
          { title: '', url: 'https://drop.com' },
          { url: 'https://no-title.com' },
        ],
      },
    })
    expect(hits).toEqual([
      {
        title: 'AI & Agents',
        url: 'https://a.com/agents',
        snippet: 'The agents report',
        extraSnippets: ['First extra.', 'Second extra.'],
      },
    ])
  })
  test('tolerates junk shapes', () => {
    expect(parseBraveWebResponse(null)).toEqual([])
    expect(parseBraveWebResponse({})).toEqual([])
    expect(parseBraveWebResponse({ web: { results: 'nope' } })).toEqual([])
  })
})

describe('parseSearxngResponse', () => {
  test('maps title/url/content/engine', () => {
    expect(
      parseSearxngResponse({
        results: [{ title: 'A', url: 'https://a.com/x', content: 'ctx', engine: 'bing' }],
      }),
    ).toEqual([{ title: 'A', url: 'https://a.com/x', snippet: 'ctx', engine: 'bing' }])
  })
})

describe('parseDuckDuckGoHtml', () => {
  test('pairs each result anchor with its snippet', () => {
    const html = `
      <a class="result__a" href="https://one.com/a">One title</a>
      <a class="result__snippet" href="https://one.com/a">First snippet text</a>
      <a class="result__a" href="https://two.com/b">Two title</a>
      <a class="result__snippet" href="https://two.com/b">Second snippet</a>`
    expect(parseDuckDuckGoHtml(html)).toEqual([
      { title: 'One title', url: 'https://one.com/a', snippet: 'First snippet text' },
      { title: 'Two title', url: 'https://two.com/b', snippet: 'Second snippet' },
    ])
  })
  test('unwraps the uddg redirect', () => {
    const html =
      '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Freal.com%2Fpage">R</a>'
    expect(parseDuckDuckGoHtml(html)[0]?.url).toBe('https://real.com/page')
  })
})

describe('registrableDomain', () => {
  test('collapses subdomains to eTLD+1', () => {
    expect(registrableDomain('https://www.bbc.co.uk/news/x')).toBe('bbc.co.uk')
    expect(registrableDomain('https://edition.cnn.com/2026/x')).toBe('cnn.com')
    expect(registrableDomain('https://blog.example.org/')).toBe('example.org')
    expect(registrableDomain('https://example.com')).toBe('example.com')
  })
})

describe('homepage / junk / navigational classification', () => {
  test('bare homepages are detected', () => {
    expect(isBareHomepage('https://www.bbc.com/')).toBe(true)
    expect(isBareHomepage('https://www.bbc.com')).toBe(true)
    expect(isBareHomepage('https://www.bbc.com/news/article-1')).toBe(false)
    expect(isBareHomepage('https://x.com/?q=1')).toBe(false)
  })
  test('navigational queries keep their homepage', () => {
    expect(isNavigationalMatch(tokenize('openai'), 'https://openai.com/')).toBe(true)
    expect(
      isNavigationalMatch(tokenize('latest ai agents 2026'), 'https://www.bbc.com/'),
    ).toBe(false)
  })
  test('aggregator shells and engine pages are junk', () => {
    expect(isJunkHit(hit({ url: 'https://news.google.de/home?hl=de' }))).toBe(true)
    expect(isJunkHit(hit({ url: 'https://duckduckgo.com/?q=x' }))).toBe(true)
    expect(isJunkHit(hit({ url: 'https://www.google.com/search?q=x' }))).toBe(true)
    expect(isJunkHit(hit({ url: 'ftp://weird.com/x' }))).toBe(true)
    expect(isJunkHit(hit({ url: 'https://support.google.com/mail/answer/1' }))).toBe(false)
    expect(isJunkHit(hit())).toBe(false)
  })
})

describe('isPubliclyRoutableUrl (content-fetch SSRF guard)', () => {
  test('rejects loopback, private ranges, and bare service names', () => {
    for (const bad of [
      'http://127.0.0.1/x',
      'http://10.0.0.5/x',
      'http://192.168.1.1/x',
      'http://172.16.0.1/x',
      'http://169.254.169.254/latest/meta-data',
      'http://localhost/x',
      'http://searxng:8080/search',
      'http://hub:4000/v1/messages',
      'file:///etc/passwd',
    ]) {
      expect(isPubliclyRoutableUrl(bad)).toBe(false)
    }
  })
  test('accepts normal public urls', () => {
    expect(isPubliclyRoutableUrl('https://www.reuters.com/tech/x')).toBe(true)
    expect(isPubliclyRoutableUrl('http://93.184.216.34/x')).toBe(true)
  })
})

describe('normalizeUrlKey', () => {
  test('drops tracking params, hash, trailing slash, www', () => {
    expect(normalizeUrlKey('https://www.a.com/path/?utm_source=x&id=2#frag')).toBe(
      'a.com/path?id=2',
    )
    expect(normalizeUrlKey('https://a.com/path')).toBe('a.com/path')
  })
})

describe('cleanResults', () => {
  // Reconstruction of the measured VPS garbage for "latest developments in
  // AI agents 2026": homepages + junk + one real article.
  const measured: RichHit[] = [
    { title: 'BBC News', url: 'https://www.bbc.com/', snippet: '', engine: 'bing' },
    { title: 'CNN', url: 'https://edition.cnn.com/', snippet: '', engine: 'bing' },
    { title: 'Google News', url: 'https://news.google.de/', snippet: '', engine: 'bing' },
    {
      title: 'AI agents in 2026: what changed',
      url: 'https://example.org/ai-agents-2026',
      snippet: 'A deep dive on AI agents and their 2026 developments',
      engine: 'bing',
    },
    { title: 'random spam page', url: 'https://spam.biz/casino', snippet: 'win big', engine: 'mwmbl' },
  ]
  test('drops homepages+junk and surfaces the real article first', () => {
    const out = cleanResults(measured, 'latest developments in AI agents 2026')
    expect(out[0]?.url).toBe('https://example.org/ai-agents-2026')
    expect(out.every(h => !isBareHomepage(h.url))).toBe(true)
    expect(out.every(h => !h.url.includes('news.google'))).toBe(true)
  })
  test('dedupes by registrable domain, keeping the better hit', () => {
    const out = cleanResults(
      [
        hit({ title: 'AI agents guide', url: 'https://docs.site.com/ai-agents', snippet: 'ai agents' }),
        hit({ title: 'Other page', url: 'https://www.site.com/other' }),
        hit({ title: 'AI agents intro', url: 'https://blog.example.com/intro', snippet: 'ai agents' }),
      ],
      'ai agents',
    )
    expect(out.map(h => registrableDomain(h.url))).toEqual(['site.com', 'example.com'])
    expect(out[0]?.title).toBe('AI agents guide')
  })
  test('dedupes exact urls that differ only by tracking noise', () => {
    const out = cleanResults(
      [
        hit({ title: 'A story', url: 'https://a.com/story?utm_source=rss' }),
        hit({ title: 'A story', url: 'https://www.a.com/story/' }),
      ],
      'story',
    )
    expect(out).toHaveLength(1)
  })
  test('preserveOrder keeps the backend ranking', () => {
    const out = cleanResults(
      [
        hit({ title: 'zzz unrelated', url: 'https://one.com/z' }),
        hit({ title: 'exact query match', url: 'https://two.com/q', snippet: 'exact query match' }),
      ],
      'exact query match',
      { preserveOrder: true },
    )
    expect(out[0]?.url).toBe('https://one.com/z')
  })
  test('navigational homepage survives', () => {
    const out = cleanResults(
      [hit({ title: 'OpenAI', url: 'https://openai.com/' })],
      'openai',
    )
    expect(out).toHaveLength(1)
  })
  test('never filters a non-empty input down to nothing', () => {
    const out = cleanResults(
      [hit({ title: 'Some Site', url: 'https://somesite.com/' })],
      'completely unrelated query',
    )
    expect(out).toHaveLength(1)
  })
  test('caps at max', () => {
    const many = Array.from({ length: 20 }, (_, i) =>
      hit({ title: `T${i}`, url: `https://site${i}.com/p` }),
    )
    expect(cleanResults(many, 'q', { max: 5 })).toHaveLength(5)
  })
})

describe('scoreHit', () => {
  test('title matches outrank snippet matches outrank none', () => {
    const q = tokenize('quantum computing breakthrough')
    const title = hit({ title: 'Quantum computing breakthrough at MIT' })
    const snippet = hit({ title: 'Tech news', snippet: 'a quantum computing breakthrough' })
    const neither = hit({ title: 'Celebrity gossip', snippet: 'red carpet' })
    expect(scoreHit(title, q)).toBeGreaterThan(scoreHit(snippet, q))
    expect(scoreHit(snippet, q)).toBeGreaterThan(scoreHit(neither, q))
  })
  test('low-trust engines sink', () => {
    const q = tokenize('anything')
    expect(scoreHit(hit({ engine: 'mwmbl' }), q)).toBeLessThan(scoreHit(hit({ engine: 'bing' }), q))
  })
})

describe('bestSnippet', () => {
  test('joins description + extra snippets, deduped and capped', () => {
    const s = bestSnippet(
      hit({ snippet: 'main desc', extraSnippets: ['main desc', 'extra detail'] }),
      100,
    )
    expect(s).toBe('main desc … extra detail')
  })
  test('empty in, empty out', () => {
    expect(bestSnippet(hit())).toBe('')
  })
})

describe('truncateAtWord', () => {
  test('cuts at a word boundary with ellipsis', () => {
    expect(truncateAtWord('alpha beta gamma delta', 16)).toBe('alpha beta…')
  })
  test('returns short strings untouched', () => {
    expect(truncateAtWord('short', 100)).toBe('short')
  })
})

describe('extractMainText', () => {
  test('prefers <article>, strips scripts/nav/entities, keeps bullets', () => {
    const filler = 'Real article text that goes on and on. '.repeat(20)
    const html = `<html><head><style>.x{}</style><script>evil()</script></head>
      <body>
        <nav><a href="/">Home</a><a href="/about">About</a></nav>
        <div>Sidebar junk everywhere</div>
        <article>
          <h1>The &amp; Headline</h1>
          <p>${filler}</p>
          <ul><li>Point one</li><li>Point two</li></ul>
        </article>
        <footer>© 2026 Junk Corp</footer>
      </body></html>`
    const text = extractMainText(html, 5000)
    expect(text).toContain('The & Headline')
    expect(text).toContain('Real article text')
    expect(text).toContain('- Point one')
    expect(text).not.toContain('evil()')
    expect(text).not.toContain('Sidebar junk')
    expect(text).not.toContain('Junk Corp')
  })
  test('falls back to <body> when no article/main, dropping header/footer', () => {
    const para = 'Body paragraph with plenty of real text in it. '.repeat(10)
    const html = `<body><header>Site chrome</header><p>${para}</p><footer>foot</footer></body>`
    const text = extractMainText(html, 5000)
    expect(text).toContain('Body paragraph')
    expect(text).not.toContain('Site chrome')
  })
  test('respects maxChars', () => {
    const html = `<article><p>${'word '.repeat(2000)}</p></article>`
    expect(extractMainText(html, 500).length).toBeLessThanOrEqual(501)
  })
})

describe('formatHitsAsDigest', () => {
  test('numbered entries with url + content, and an answer-now instruction', () => {
    const digest = formatHitsAsDigest('my query', [
      hit({ title: 'A', url: 'https://a.com/x', content: 'Fetched body text.' }),
      hit({ title: 'B', url: 'https://b.com/y', snippet: 'only a snippet' }),
    ])
    expect(digest).toContain('[1] A')
    expect(digest).toContain('URL: https://a.com/x')
    expect(digest).toContain('Fetched body text.')
    expect(digest).toContain('[2] B')
    expect(digest).toContain('only a snippet')
    expect(digest).toContain('cite the sources')
  })
})
