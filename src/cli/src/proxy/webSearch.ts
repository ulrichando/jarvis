// Web-search short-circuit for Anthropic's server-side web_search tool.
//
// The first-party WebSearchTool (src/tools/WebSearchTool) sends an Anthropic
// request carrying a tool schema of { type: 'web_search_20250305', ... } and
// expects the stream back to contain `server_tool_use` + `web_search_tool_result`
// content blocks. That only works against api.anthropic.com. When routed through
// this proxy to a non-Anthropic provider, no search ever runs and the UI reports
// "Did 0 searches".
//
// We intercept the request at the proxy and run the real search pipeline
// (src/proxy/search/): Brave Search API when BRAVE_SEARCH_API_KEY is set,
// falling back to SearXNG → DuckDuckGo keyless — then clean/dedup/rerank the
// hits, fetch the top pages for real content, and synthesize the Anthropic
// streaming events the client already knows how to parse. Alongside the
// spec-shaped web_search_result blocks (title/url the phone renders as source
// bubbles) we emit a trailing `text` block carrying the content digest — the
// CLI's WebSearchTool folds trailing text into the tool result, so the model
// finally sees page text instead of bare links and answers after ONE search.

import { bestSnippet, formatHitsAsDigest, type RichHit } from './search/core.js'
import {
  enrichHitsWithContent,
  runSearchPipeline,
  searchDuckDuckGo as pipelineSearchDuckDuckGo,
  searchSearxng as pipelineSearchSearxng,
  type SearchProvider,
} from './search/pipeline.js'

export { parseDuckDuckGoHtml } from './search/core.js'
export type { RichHit, SearchProvider }

export type SearchHit = { title: string; url: string }

// Back-compat single-arg wrappers (the historical exports of this module).
export async function searchSearxng(query: string): Promise<RichHit[]> {
  return pipelineSearchSearxng(query)
}

export async function searchDuckDuckGo(query: string): Promise<RichHit[]> {
  return pipelineSearchDuckDuckGo(query)
}

// Top-level entry: Brave when keyed, else SearXNG when configured, else
// DuckDuckGo — cleaned/deduped/reranked. Throws only when every backend
// fails — callers surface that as a web_search_tool_result_error (not a
// silent empty).
export async function webSearch(query: string): Promise<RichHit[]> {
  return (await webSearchWithProvider(query)).hits
}

async function webSearchWithProvider(
  query: string,
): Promise<{ hits: RichHit[]; provider: SearchProvider }> {
  return runSearchPipeline(query, {
    log: m => console.warn(`[jarvis-proxy] ${m}`),
  })
}

export type WebResearchResult = { hits: RichHit[]; provider: SearchProvider }

// Full research pass: search + clean, then fetch the top pages in parallel
// (per-page timeout + overall budget — see search/pipeline.ts) so the model
// gets real article text, not 90-char snippets.
export async function webResearch(query: string): Promise<WebResearchResult> {
  const { hits, provider } = await webSearchWithProvider(query)
  const enriched = await enrichHitsWithContent(hits)
  return { hits: enriched, provider }
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

// Spec-shaped web_search_result items. `snippet` is a jarvis extension the
// official shape doesn't carry — existing clients (CLI WebSearchTool, the
// Android chat) parse only {title,url} with unknown-key tolerance, so it's
// forward-compatible enrichment, not a break.
function webSearchResultItems(hits: RichHit[]): unknown[] {
  return hits.slice(0, 10).map(h => {
    const snippet = bestSnippet(h, 300)
    return {
      type: 'web_search_result',
      title: h.title,
      url: h.url,
      ...(snippet ? { snippet } : {}),
      encrypted_content: '',
      page_age: null,
    }
  })
}

export async function writeSyntheticWebSearchStream(
  query: string,
  model: string,
  controller: ReadableStreamDefaultController<Uint8Array>,
): Promise<SearchProvider | 'failed'> {
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

  // 2. Execute the actual research pass (search + content fetch).
  let hits: RichHit[] = []
  let provider: SearchProvider | 'failed' = 'failed'
  try {
    const result = await webResearch(query)
    hits = result.hits
    provider = result.provider
  } catch (e) {
    console.error('[jarvis-proxy] web search failed:', e)
  }

  // 3. web_search_tool_result block
  const resultContent =
    provider === 'failed'
      ? { type: 'web_search_tool_result_error', error_code: 'unavailable' }
      : webSearchResultItems(hits)

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

  // 4. Trailing text block: the research digest (titles + page content +
  //    urls). WebSearchTool folds text-after-results into the tool result,
  //    so this is what lets the model answer from ONE search.
  if (provider !== 'failed' && hits.length > 0) {
    send('content_block_start', {
      type: 'content_block_start',
      index: 2,
      content_block: { type: 'text', text: '' },
    })
    send('content_block_delta', {
      type: 'content_block_delta',
      index: 2,
      delta: { type: 'text_delta', text: formatHitsAsDigest(query, hits) },
    })
    send('content_block_stop', { type: 'content_block_stop', index: 2 })
  }

  // 5. Close the message
  send('message_delta', {
    type: 'message_delta',
    delta: { stop_reason: 'end_turn', stop_sequence: null },
    usage: { output_tokens: 0 },
  })
  send('message_stop', { type: 'message_stop' })

  return provider
}

export function buildSyntheticWebSearchResponse(
  query: string,
  model: string,
  hits: RichHit[],
  failed: boolean,
): unknown {
  const toolUseId = randomId('srvtoolu_')
  const content: unknown[] = [
    {
      type: 'server_tool_use',
      id: toolUseId,
      name: 'web_search',
      input: { query },
    },
    {
      type: 'web_search_tool_result',
      tool_use_id: toolUseId,
      content: failed
        ? { type: 'web_search_tool_result_error', error_code: 'unavailable' }
        : webSearchResultItems(hits),
    },
  ]
  if (!failed && hits.length > 0) {
    content.push({ type: 'text', text: formatHitsAsDigest(query, hits) })
  }

  return {
    id: randomId('msg_'),
    type: 'message',
    role: 'assistant',
    model,
    content,
    stop_reason: 'end_turn',
    stop_sequence: null,
    usage: { input_tokens: 0, output_tokens: 0 },
  }
}
