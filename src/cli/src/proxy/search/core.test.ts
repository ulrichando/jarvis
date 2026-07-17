import { describe, expect, test } from 'bun:test'
import {
  bestSnippet,
  bestSupportingPassage,
  buildRewritePrompt,
  cleanResults,
  extractMainText,
  formatHitsAsDigest,
  heuristicQueryRewrite,
  isBareHomepage,
  isJunkHit,
  isNavigationalMatch,
  isPubliclyRoutableUrl,
  mergeHits,
  normalizeUrlKey,
  parseBraveWebResponse,
  parseDuckDuckGoHtml,
  parseLlmQueryRewrite,
  parseSearxngResponse,
  registrableDomain,
  scoreHit,
  stripCourtesyPrefix,
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

describe('formatHitsAsDigest — citation-grounded evidence', () => {
  test('numbered entries with url + content, citation + abstention contract', () => {
    const digest = formatHitsAsDigest('my query', [
      hit({ title: 'A', url: 'https://a.com/x', content: 'Fetched body text.' }),
      hit({ title: 'B', url: 'https://b.com/y', snippet: 'only a snippet' }),
    ])
    expect(digest).toContain('[1] A')
    expect(digest).toContain('URL: https://a.com/x')
    expect(digest).toContain('Fetched body text.')
    expect(digest).toContain('[2] B')
    expect(digest).toContain('only a snippet')
    // Claude-style inline attribution with preserved URLs…
    expect(digest).toContain('Attribute each substantive claim to its source')
    expect(digest).toContain('Preserve the exact URLs')
    // …and the abstention clause: no reliable evidence → say so, never invent.
    expect(digest).toContain("couldn't find reliable sources")
    expect(digest).toContain('do not search again')
  })
  test('caps the evidence at 8 numbered chunks', () => {
    const many = Array.from({ length: 12 }, (_, i) =>
      hit({ title: `T${i}`, url: `https://s${i}.com/p`, snippet: `s${i}` }),
    )
    const digest = formatHitsAsDigest('q', many)
    expect(digest).toContain('[8] T7')
    expect(digest).not.toContain('[9]')
  })
})

describe('stripCourtesyPrefix / heuristicQueryRewrite — stage 1 (pure)', () => {
  test('strips conversational courtesy prefixes', () => {
    expect(
      stripCourtesyPrefix('hey jarvis can you tell me about the weather in tokyo'),
    ).toBe('the weather in tokyo')
    expect(stripCourtesyPrefix('please look up the gdp of france')).toBe(
      'the gdp of france',
    )
    expect(stripCourtesyPrefix('search the web for rust async traits')).toBe(
      'rust async traits',
    )
  })
  test('never strips a query down to nothing', () => {
    expect(stripCourtesyPrefix('tell me')).toBe('tell me')
  })
  test('a plain retrieval query passes through untouched', () => {
    expect(stripCourtesyPrefix('rust async traits 2026')).toBe('rust async traits 2026')
    expect(heuristicQueryRewrite('rust async traits 2026')).toEqual([
      'rust async traits 2026',
    ])
  })
  test('splits an explicit two-question input', () => {
    expect(
      heuristicQueryRewrite('who won the champions league final? what was the score'),
    ).toEqual(['who won the champions league final?', 'what was the score'])
    expect(
      heuristicQueryRewrite('what is the capital of australia and how many people live there'),
    ).toEqual(['what is the capital of australia', 'how many people live there'])
  })
  test('plain conjunctions never split (conservative)', () => {
    expect(heuristicQueryRewrite('research on ai and machine learning')).toEqual([
      'research on ai and machine learning',
    ])
    expect(heuristicQueryRewrite('fish and chips near me')).toEqual([
      'fish and chips near me',
    ])
  })
  test('caps at maxQueries', () => {
    const qs = heuristicQueryRewrite(
      'what is a? what is b? what is c? what is d?',
      2,
    )
    expect(qs.length).toBeLessThanOrEqual(2)
  })
  test('maxQueries 1 → single cleaned query', () => {
    expect(heuristicQueryRewrite('who won? what was the score', 1)).toEqual([
      'who won? what was the score',
    ])
  })
})

describe('parseLlmQueryRewrite', () => {
  test('parses a JSON array (also when code-fenced)', () => {
    expect(parseLlmQueryRewrite('["ai agents 2026", "agent frameworks"]', 'orig')).toEqual([
      'ai agents 2026',
      'agent frameworks',
    ])
    expect(
      parseLlmQueryRewrite('```json\n["quantum computing"]\n```', 'orig'),
    ).toEqual(['quantum computing'])
  })
  test('parses numbered/bulleted lines, skipping prose', () => {
    expect(
      parseLlmQueryRewrite(
        'Here are the queries:\n1. brave search api pricing\n- searxng setup guide',
        'orig',
      ),
    ).toEqual(['brave search api pricing', 'searxng setup guide'])
  })
  test('garbage degrades to the original query', () => {
    expect(parseLlmQueryRewrite('', 'orig')).toEqual(['orig'])
    expect(parseLlmQueryRewrite('   \n  ', 'orig')).toEqual(['orig'])
  })
  test('caps at maxQueries and dedups case-insensitively', () => {
    expect(
      parseLlmQueryRewrite('["a query", "A Query", "b query", "c query"]', 'orig', 2),
    ).toEqual(['a query', 'b query'])
  })
})

describe('buildRewritePrompt', () => {
  test('embeds the question and the cap, demands a JSON array', () => {
    const p = buildRewritePrompt('why is the sky blue', 2)
    expect(p).toContain('why is the sky blue')
    expect(p).toContain('at most 2')
    expect(p).toContain('JSON array')
  })
})

describe('mergeHits — multi-query merge', () => {
  test('round-robins across lists and dedups url + domain', () => {
    const merged = mergeHits(
      [
        [
          hit({ title: 'A1', url: 'https://a.com/1' }),
          hit({ title: 'A2', url: 'https://shared.com/x' }),
        ],
        [
          hit({ title: 'B1', url: 'https://b.com/1' }),
          hit({ title: 'B2', url: 'https://shared.com/x?utm_source=rss' }),
          hit({ title: 'B3', url: 'https://c.com/1' }),
        ],
      ],
      10,
    )
    expect(merged.map(h => h.title)).toEqual(['A1', 'B1', 'A2', 'B3'])
  })
  test('caps at max', () => {
    const list = (n: string) =>
      Array.from({ length: 5 }, (_, i) => hit({ title: `${n}${i}`, url: `https://${n}${i}.com/p` }))
    expect(mergeHits([list('x'), list('y')], 4)).toHaveLength(4)
  })
})

describe('bestSupportingPassage — cited_text selection', () => {
  test('picks the content region with the best query overlap', () => {
    const h = hit({
      content: [
        'Intro paragraph about nothing in particular, filler filler filler.',
        'The launch date was confirmed as March 12 by the space agency officials.',
        'Unrelated closing remarks and site boilerplate text goes here now.',
      ].join('\n'),
    })
    const passage = bestSupportingPassage(h, 'when is the launch date', 200)
    expect(passage).toContain('March 12')
    expect(passage.startsWith('The launch date')).toBe(true)
  })
  test('falls back to snippets when there is no fetched content', () => {
    const h = hit({ snippet: 'short teaser', extraSnippets: ['extra detail'] })
    expect(bestSupportingPassage(h, 'anything', 200)).toBe('short teaser … extra detail')
  })
  test('no token overlap → leading content, capped', () => {
    const h = hit({ content: 'word '.repeat(500) })
    const p = bestSupportingPassage(h, 'zzz qqq', 100)
    expect(p.length).toBeLessThanOrEqual(101)
  })
})
