// DuckDuckGo-backed short-circuit for Anthropic's server-side web_search tool.
//
// The first-party WebSearchTool (src/tools/WebSearchTool) sends an Anthropic
// request carrying a tool schema of { type: 'web_search_20250305', ... } and
// expects the stream back to contain `server_tool_use` + `web_search_tool_result`
// content blocks. That only works against api.anthropic.com. When routed through
// this proxy to a non-Anthropic provider, no search ever runs and the UI reports
// "Did 0 searches".
//
// We intercept the request at the proxy, execute a real DDG HTML search
// ourselves, and synthesize the Anthropic streaming events the client already
// knows how to parse.

export type SearchHit = { title: string; url: string }

const DDG_ENDPOINT = 'https://html.duckduckgo.com/html/'
const USER_AGENT =
  'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

// Primary backend: self-hosted SearXNG (same SEARXNG_URL the voice stack uses).
// The DuckDuckGo HTML endpoint CAPTCHA-blocks datacenter/VPS IPs — the voice
// stack already hit this and moved to SearXNG; the CLI proxy was left on DDG.
export async function searchSearxng(query: string): Promise<SearchHit[]> {
  const base = (process.env.SEARXNG_URL ?? '').trim().replace(/\/+$/, '')
  if (!base) throw new Error('SEARXNG_URL not set')
  // SearXNG needs `search.formats: [html, json]` server-side or /search?format=json 403s.
  const url = `${base}/search?q=${encodeURIComponent(query)}&format=json&pageno=1`
  const res = await fetch(url, { headers: { Accept: 'application/json' } })
  if (!res.ok) throw new Error(`SearXNG HTTP ${res.status}`)
  const data: any = await res.json()
  const results = Array.isArray(data?.results) ? data.results : []
  return results
    .map((r: any) => ({
      title: String(r?.title ?? '').trim(),
      url: String(r?.url ?? '').trim(),
    }))
    .filter((h: SearchHit) => h.title && h.url)
    .slice(0, 10)
}

// A DuckDuckGo anti-bot / CAPTCHA interstitial returns HTTP 200 with NO result
// anchors — so an empty parse looked like a clean "0 results". Detect the block
// page by its markers so the caller can report the search as FAILED (the model
// then knows the search was blocked, not that the web has nothing).
function looksLikeDuckDuckGoBlockPage(html: string): boolean {
  const h = html.toLowerCase()
  return (
    h.includes('anomaly') ||
    h.includes('challenge-form') ||
    h.includes('detected unusual') ||
    h.includes('unusual traffic') ||
    h.includes('are you a robot') ||
    h.includes('captcha')
  )
}

export async function searchDuckDuckGo(query: string): Promise<SearchHit[]> {
  const url = `${DDG_ENDPOINT}?q=${encodeURIComponent(query)}`
  const res = await fetch(url, {
    method: 'POST',
    headers: {
      'User-Agent': USER_AGENT,
      'Accept': 'text/html,application/xhtml+xml',
      'Accept-Language': 'en-US,en;q=0.9',
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: `q=${encodeURIComponent(query)}&b=&kl=us-en`,
  })
  if (!res.ok) throw new Error(`DuckDuckGo HTTP ${res.status}`)
  const html = await res.text()
  const hits = parseDuckDuckGoHtml(html)
  if (hits.length === 0 && looksLikeDuckDuckGoBlockPage(html)) {
    // THROW rather than return [] — a blocked search must not masquerade as
    // "no results exist" (the exact silent-failure the sweep flagged).
    throw new Error('DuckDuckGo returned an anti-bot/CAPTCHA page (search blocked)')
  }
  return hits
}

// Top-level entry the proxy uses: prefer SearXNG when configured, fall back to
// DuckDuckGo. Throws if the chosen backend(s) fail — callers surface that as a
// web_search_tool_result_error (not a silent empty).
export async function webSearch(query: string): Promise<SearchHit[]> {
  if ((process.env.SEARXNG_URL ?? '').trim()) {
    try {
      return await searchSearxng(query)
    } catch (e) {
      console.warn(
        '[jarvis-proxy] SearXNG search failed, falling back to DuckDuckGo:',
        (e as Error).message,
      )
    }
  }
  return searchDuckDuckGo(query)
}

export function parseDuckDuckGoHtml(html: string): SearchHit[] {
  const hits: SearchHit[] = []
  const seen = new Set<string>()
  const anchorRe =
    /<a[^>]+class="[^"]*\bresult__a\b[^"]*"[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/gi

  let m: RegExpExecArray | null
  while ((m = anchorRe.exec(html)) !== null) {
    const href = decodeHtmlEntities(m[1])
    const titleHtml = m[2]
    const url = unwrapDuckDuckGoRedirect(href)
    const title = decodeHtmlEntities(stripTags(titleHtml)).replace(/\s+/g, ' ').trim()
    if (!url || !title) continue
    if (seen.has(url)) continue
    seen.add(url)
    hits.push({ title, url })
  }
  return hits
}

function unwrapDuckDuckGoRedirect(href: string): string {
  try {
    const absolute = href.startsWith('//') ? 'https:' + href : href
    const parsed = new URL(absolute, 'https://duckduckgo.com/')
    const uddg = parsed.searchParams.get('uddg')
    if (uddg) return decodeURIComponent(uddg)
    return parsed.toString()
  } catch {
    return href
  }
}

function stripTags(s: string): string {
  // Loop until stable — a single pass lets `<<a>script>` collapse into a fresh
  // `<script>` tag (js/incomplete-multi-character-sanitization).
  let prev: string
  do {
    prev = s
    s = s.replace(/<[^>]+>/g, '')
  } while (s !== prev)
  return s
}

function decodeHtmlEntities(s: string): string {
  // Decode &amp; LAST: doing it first turns `&amp;lt;` into `&lt;` then `<`
  // (double-decoding). Last keeps `&amp;lt;` → `&lt;` (js/double-escaping).
  return s
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&#x27;/gi, "'")
    .replace(/&nbsp;/g, ' ')
    .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(parseInt(n, 10)))
    .replace(/&#x([0-9a-f]+);/gi, (_, h) => String.fromCharCode(parseInt(h, 16)))
    .replace(/&amp;/g, '&')
}

// ── Detect a WebSearchTool inner request ──────────────────────────────────
//
// WebSearchTool.call() pushes { type: 'web_search_20250305', name: 'web_search' }
// into extraToolSchemas. No other code path adds that type, so its presence in
// the outgoing request uniquely identifies "this is a web search tool call".

const WEB_SEARCH_USER_PREFIX = 'Perform a web search for the query: '

export function extractWebSearchQuery(anthropicReq: any): string | null {
  const tools = anthropicReq?.tools
  if (!Array.isArray(tools)) return null
  if (!tools.some((t: any) => t?.type === 'web_search_20250305')) return null

  const messages = anthropicReq?.messages
  if (!Array.isArray(messages)) return null

  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i]
    if (msg?.role !== 'user') continue
    const text = userMessageText(msg.content)
    if (!text) continue
    return text.startsWith(WEB_SEARCH_USER_PREFIX)
      ? text.slice(WEB_SEARCH_USER_PREFIX.length).trim()
      : text.trim()
  }
  return null
}

function userMessageText(content: unknown): string {
  if (typeof content === 'string') return content
  if (Array.isArray(content)) {
    return content
      .filter((b: any) => b?.type === 'text')
      .map((b: any) => b?.text ?? '')
      .join('')
  }
  return ''
}

// ── Synthetic Anthropic stream ────────────────────────────────────────────

function sseEvent(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
}

function randomId(prefix: string): string {
  return prefix + Math.random().toString(36).slice(2, 14)
}

export async function writeSyntheticWebSearchStream(
  query: string,
  model: string,
  controller: ReadableStreamDefaultController<Uint8Array>,
): Promise<void> {
  const enc = new TextEncoder()
  const send = (event: string, data: unknown) => {
    controller.enqueue(enc.encode(sseEvent(event, data)))
  }

  const messageId = randomId('msg_')
  const toolUseId = randomId('srvtoolu_')

  send('message_start', {
    type: 'message_start',
    message: {
      id: messageId,
      type: 'message',
      role: 'assistant',
      content: [],
      model,
      stop_reason: null,
      stop_sequence: null,
      usage: { input_tokens: 0, output_tokens: 0 },
    },
  })

  send('ping', { type: 'ping' })

  // 1. server_tool_use block — carries the query the client will surface
  //    as "Searching: <query>" progress text.
  send('content_block_start', {
    type: 'content_block_start',
    index: 0,
    content_block: {
      type: 'server_tool_use',
      id: toolUseId,
      name: 'web_search',
      input: {},
    },
  })
  send('content_block_delta', {
    type: 'content_block_delta',
    index: 0,
    delta: {
      type: 'input_json_delta',
      partial_json: JSON.stringify({ query }),
    },
  })
  send('content_block_stop', { type: 'content_block_stop', index: 0 })

  // 2. Execute the actual search
  let hits: SearchHit[] = []
  let failed = false
  try {
    hits = await webSearch(query)
  } catch (e) {
    console.error('[jarvis-proxy] web search failed:', e)
    failed = true
  }

  // 3. web_search_tool_result block
  const resultContent = failed
    ? { type: 'web_search_tool_result_error', error_code: 'unavailable' }
    : hits.slice(0, 10).map(h => ({
        type: 'web_search_result',
        title: h.title,
        url: h.url,
        encrypted_content: '',
        page_age: null,
      }))

  send('content_block_start', {
    type: 'content_block_start',
    index: 1,
    content_block: {
      type: 'web_search_tool_result',
      tool_use_id: toolUseId,
      content: resultContent,
    },
  })
  send('content_block_stop', { type: 'content_block_stop', index: 1 })

  // 4. Close the message
  send('message_delta', {
    type: 'message_delta',
    delta: { stop_reason: 'end_turn', stop_sequence: null },
    usage: { output_tokens: 0 },
  })
  send('message_stop', { type: 'message_stop' })
}

export function buildSyntheticWebSearchResponse(
  query: string,
  model: string,
  hits: SearchHit[],
  failed: boolean,
): unknown {
  const toolUseId = randomId('srvtoolu_')
  const content = failed
    ? { type: 'web_search_tool_result_error', error_code: 'unavailable' }
    : hits.slice(0, 10).map(h => ({
        type: 'web_search_result',
        title: h.title,
        url: h.url,
        encrypted_content: '',
        page_age: null,
      }))

  return {
    id: randomId('msg_'),
    type: 'message',
    role: 'assistant',
    model,
    content: [
      {
        type: 'server_tool_use',
        id: toolUseId,
        name: 'web_search',
        input: { query },
      },
      {
        type: 'web_search_tool_result',
        tool_use_id: toolUseId,
        content,
      },
    ],
    stop_reason: 'end_turn',
    stop_sequence: null,
    usage: { input_tokens: 0, output_tokens: 0 },
  }
}
