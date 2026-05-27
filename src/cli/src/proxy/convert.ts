import type { Provider } from './providers.js'
import { getReasoning, setReasoning, REASONING_PLACEHOLDER } from './reasoning-cache.js'

// ── Reasoning-content round-trip ────────────────────────────────────────────
//
// DeepSeek's thinking-mode API requires the prior assistant turn's
// reasoning_content to be echoed back on follow-up turns. Anthropic's
// protocol has no field for this. The proxy caches reasoning_content
// server-side keyed by tool_use_id (which round-trips faithfully through
// Claude Code) and re-attaches it to the outgoing OpenAI request.
//
// See ./reasoning-cache.ts for the storage layer. Cache writes happen in
// stream.ts (streaming path) and convertResponse below (non-streaming);
// cache reads happen in convertMessages below.

// ── OpenAI message types ───────────────────────────────────────────────────

export type OpenAIPart =
  | { type: 'text'; text: string }
  | { type: 'image_url'; image_url: { url: string } }

type OpenAIMessage =
  | { role: 'system'; content: string }
  // Vision-capable providers accept an array of typed parts as user
  // content. Non-vision providers see flat strings (with images
  // replaced by the literal "[image]" placeholder).
  | { role: 'user'; content: string | OpenAIPart[] }
  | {
      role: 'assistant'
      content: string | null
      tool_calls?: OpenAIToolCall[]
      // DeepSeek thinking-mode requires the prior assistant turn's
      // chain-of-thought to be echoed back on follow-up turns. We
      // round-trip it through Anthropic's `thinking` content block
      // (see convertResponse / convertMessages). Other OpenAI-compat
      // providers silently ignore the extra field.
      reasoning_content?: string
    }
  | { role: 'tool'; tool_call_id: string; content: string }

type OpenAIToolCall = {
  id: string
  type: 'function'
  function: { name: string; arguments: string }
}

type OpenAITool = {
  type: 'function'
  function: { name: string; description: string; parameters: unknown }
}

// ── Convert Anthropic system prompt to string ──────────────────────────────

function extractSystemText(system: unknown): string {
  if (!system) return ''
  if (typeof system === 'string') return system
  if (Array.isArray(system)) {
    return system
      .filter((b: any) => b.type === 'text')
      .map((b: any) => b.text ?? '')
      .join('\n')
  }
  return ''
}

// ── <think>-tag stripping (qwen3 + other open-source reasoning models) ──

// Strip <think>...</think> blocks (and one trailing newline run) from a
// finished text response. Some models — primarily Qwen3 on Groq — emit
// chain-of-thought inside literal <think> tags in the visible content
// instead of in a separate reasoning_content field. The CLI doesn't
// render the tags specially, so the user sees "<think>maybe I should…"
// in their chat. This regex removes the blocks. Safe to call on any
// text — <think> isn't a real HTML tag.
export function stripThinkTags(text: string): string {
  return text.replace(/<think>[\s\S]*?<\/think>\n*/g, '')
}

// Streaming variant: an SSE-friendly state machine. Feed it chunks of
// text; it emits the visible (non-think) portion and holds back any
// bytes that might be the start of a <think> / </think> tag pair
// crossing a chunk boundary. Call end() when the stream finishes —
// returns any held-back bytes that turned out not to be tags (or
// empty if the stream ended inside an unclosed think block).
export class ThinkTagStripper {
  private inThink = false
  private buffer = ''

  feed(chunk: string): string {
    this.buffer += chunk
    let out = ''
    while (this.buffer.length > 0) {
      if (this.inThink) {
        const closeIdx = this.buffer.indexOf('</think>')
        if (closeIdx !== -1) {
          this.buffer = this.buffer.slice(closeIdx + '</think>'.length)
          this.inThink = false
          this.buffer = this.buffer.replace(/^\n+/, '')
          continue
        }
        // Hold back any tail bytes that COULD be the start of </think>;
        // discard everything else (it's still inside the think block).
        const partial = endsWithPartialOf(this.buffer, '</think>')
        this.buffer = this.buffer.slice(this.buffer.length - partial)
        break
      }
      const openIdx = this.buffer.indexOf('<think>')
      if (openIdx !== -1) {
        out += this.buffer.slice(0, openIdx)
        this.buffer = this.buffer.slice(openIdx + '<think>'.length)
        this.inThink = true
        continue
      }
      // No full open tag in buffer. Emit everything except a possible
      // partial <think> at the tail (kept for the next feed()).
      const partial = endsWithPartialOf(this.buffer, '<think>')
      if (partial > 0) {
        out += this.buffer.slice(0, this.buffer.length - partial)
        this.buffer = this.buffer.slice(this.buffer.length - partial)
      } else {
        out += this.buffer
        this.buffer = ''
      }
      break
    }
    return out
  }

  end(): string {
    // Stream ended. Inside a think block: discard whatever's left.
    // Outside: the held bytes were a false-alarm partial tag, emit them.
    if (this.inThink) {
      this.buffer = ''
      return ''
    }
    const out = this.buffer
    this.buffer = ''
    return out
  }
}

function endsWithPartialOf(text: string, target: string): number {
  // Longest suffix of `text` that is a prefix of `target`. Used to
  // decide how many trailing bytes to hold back across chunk seams.
  const maxLen = Math.min(text.length, target.length - 1)
  for (let len = maxLen; len > 0; len--) {
    if (target.startsWith(text.slice(text.length - len))) return len
  }
  return 0
}

// Gate: which models need the <think> strip applied? Today only Qwen3
// (Groq). Other open-source / reasoning models use a separate
// reasoning_content field and don't leak tags into visible content.
export function modelLeaksThinkTags(modelId: string): boolean {
  return modelId.includes('qwen')
}

// ── Convert a single Anthropic content block to text ─────────────────────

function contentToText(content: unknown): string {
  if (!content) return ''
  if (typeof content === 'string') return content
  if (Array.isArray(content)) {
    return content
      .map((b: any) => {
        if (b.type === 'text') return b.text ?? ''
        if (b.type === 'image') return '[image]'
        return ''
      })
      .join('')
  }
  if (typeof content === 'object' && (content as any).type === 'text') {
    return (content as any).text ?? ''
  }
  return ''
}

// Vision-aware variant. Returns a plain string (current behaviour) when
// there are no images OR the upstream provider can't see them. When
// images ARE present and the provider supports vision, returns the
// OpenAI-shape array of {type:'text'|'image_url'} parts so the upstream
// LLM actually receives pixels instead of the literal "[image]".
//
// Edge cases the function defends:
// - image-only message → prepends `{type:'text', text:''}` so providers
//   that require ≥1 text part don't reject the message
// - unknown image.source.type → falls back to `[image]` placeholder text
//   part (and never throws)
// - non-array content → delegates to contentToText (existing semantics)
export function contentToOpenAIParts(
  content: unknown,
  supportsVision: boolean,
): string | OpenAIPart[] {
  if (!content) return ''
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return contentToText(content)

  const hasImage = content.some((b: any) => b && b.type === 'image')
  if (!hasImage || !supportsVision) return contentToText(content)

  const parts: OpenAIPart[] = []
  for (const b of content) {
    if (!b) continue
    if (b.type === 'text') {
      if (b.text) parts.push({ type: 'text', text: b.text })
      continue
    }
    if (b.type === 'image') {
      const src = b.source
      if (src && src.type === 'base64' && src.data && src.media_type) {
        parts.push({
          type: 'image_url',
          image_url: { url: `data:${src.media_type};base64,${src.data}` },
        })
      } else if (src && src.type === 'url' && src.url) {
        parts.push({ type: 'image_url', image_url: { url: src.url } })
      } else {
        // Unknown / missing source — drop pixels but keep a textual
        // breadcrumb so the model knows something visual was there.
        parts.push({ type: 'text', text: '[image]' })
      }
      continue
    }
    // Other Anthropic block types (thinking, tool_use, …) aren't valid
    // in a user turn — silently ignore rather than emit garbage.
  }

  // Some providers reject content arrays with zero text parts. Prepend
  // an empty text part so an image-only user message round-trips.
  if (!parts.some((p) => p.type === 'text')) {
    parts.unshift({ type: 'text', text: '' })
  }

  return parts.length > 0 ? parts : ''
}

// ── Convert Anthropic messages → OpenAI messages ──────────────────────────

export function convertMessages(
  anthropicMessages: any[],
  requiresReasoning = false,
  supportsVision = false,
): OpenAIMessage[] {
  const out: OpenAIMessage[] = []

  for (const msg of anthropicMessages) {
    if (msg.role === 'assistant') {
      const content = msg.content
      if (typeof content === 'string') {
        const m: any = { role: 'assistant', content }
        if (requiresReasoning) m.reasoning_content = REASONING_PLACEHOLDER
        out.push(m)
        continue
      }
      if (!Array.isArray(content)) continue

      const textBlocks = content.filter((b: any) => b.type === 'text')
      const toolUseBlocks = content.filter((b: any) => b.type === 'tool_use')
      const thinkingBlocks = content.filter((b: any) => b.type === 'thinking')
      // Reconstitute reasoning_content. Two sources, in priority order:
      // 1. Server-side cache keyed by tool_use_id.
      // 2. Thinking blocks (kept for clients that pass them through).
      // Falls back to a placeholder when requiresReasoning is true so
      // thinking-mode upstreams don't 400 on cache miss.
      let reasoning = ''
      const text = textBlocks.map((b: any) => b.text ?? '').join('') || null
      for (const tu of toolUseBlocks) {
        const cached = getReasoning(tu.id)
        if (cached) {
          reasoning = cached
          break
        }
      }
      if (!reasoning) {
        reasoning = thinkingBlocks.map((b: any) => b.thinking ?? '').join('')
      }
      if (!reasoning && requiresReasoning) {
        reasoning = REASONING_PLACEHOLDER
      }

      const assistantMsg: any = { role: 'assistant', content: text ?? '' }
      if (reasoning) assistantMsg.reasoning_content = reasoning
      if (toolUseBlocks.length > 0) {
        assistantMsg.content = text
        assistantMsg.tool_calls = toolUseBlocks.map((b: any) => ({
          id: b.id,
          type: 'function' as const,
          function: {
            name: b.name,
            arguments: typeof b.input === 'string' ? b.input : JSON.stringify(b.input ?? {}),
          },
        })) as OpenAIToolCall[]
      }
      out.push(assistantMsg)
    } else if (msg.role === 'user') {
      const content = msg.content
      if (typeof content === 'string') {
        out.push({ role: 'user', content })
        continue
      }
      if (!Array.isArray(content)) continue

      const toolResults = content.filter((b: any) => b.type === 'tool_result')
      const otherBlocks = content.filter((b: any) => b.type !== 'tool_result')

      // Each tool_result becomes a separate role:tool message
      for (const tr of toolResults) {
        const resultText = tr.is_error
          ? '[ERROR] ' + contentToText(tr.content)
          : contentToText(tr.content)
        out.push({ role: 'tool', tool_call_id: tr.tool_use_id, content: resultText })
      }

      // Remaining blocks (text + image) become a user message. Goes
      // through contentToOpenAIParts so image blocks survive into a
      // typed `image_url` part when the upstream supports vision, or
      // flatten to a `[image]` placeholder string when it doesn't.
      // Pre-Step-3 the code filtered to text-only and silently dropped
      // image blocks — that's the bug this replaces.
      if (otherBlocks.length > 0) {
        const userContent = contentToOpenAIParts(otherBlocks, supportsVision)
        const hasContent =
          typeof userContent === 'string'
            ? userContent.length > 0
            : userContent.length > 0
        if (hasContent) out.push({ role: 'user', content: userContent })
      }
    }
  }

  return out
}

// ── Repair: ensure every tool_call has a matching tool result ─────────────
// This fixes the DeepSeek "insufficient tool messages" error.

export function repairMessageSequence(messages: OpenAIMessage[]): OpenAIMessage[] {
  const out: OpenAIMessage[] = []
  let i = 0
  while (i < messages.length) {
    const msg = messages[i]
    out.push(msg)
    if (msg.role === 'assistant' && msg.tool_calls && msg.tool_calls.length > 0) {
      // Collect all tool messages that immediately follow
      const coveredIds = new Set<string>()
      let j = i + 1
      while (j < messages.length && messages[j].role === 'tool') {
        coveredIds.add((messages[j] as any).tool_call_id)
        out.push(messages[j])
        j++
      }
      // Insert placeholders for any missing tool_call_ids
      for (const tc of msg.tool_calls) {
        if (!coveredIds.has(tc.id)) {
          out.push({
            role: 'tool',
            tool_call_id: tc.id,
            content: '[ERROR] Tool call was interrupted or did not complete.',
          })
        }
      }
      i = j
    } else {
      i++
    }
  }
  return out
}

// ── Convert Anthropic tools → OpenAI tools ────────────────────────────────

const STRIP_TOOL_KEYS = new Set([
  'cache_control', 'defer_loading', 'eager_input_streaming',
])

export function convertTools(anthropicTools: any[], provider: Provider): OpenAITool[] {
  let tools = anthropicTools.map((t: any) => ({
    type: 'function' as const,
    function: {
      name: t.name,
      description: t.description ?? '',
      parameters: t.input_schema ?? { type: 'object', properties: {} },
    },
  }))

  // Groq: cap at 20 tools, prioritize the most useful ones
  if (provider.maxTools && tools.length > provider.maxTools) {
    const PRIORITY = new Set(['bash', 'read_file', 'write_file', 'edit_file', 'glob', 'grep', 'web_search', 'web_fetch', 'think', 'dispatch', 'ask_user', 'todo_write'])
    const priority = tools.filter(t => PRIORITY.has(t.function.name))
    const rest = tools.filter(t => !PRIORITY.has(t.function.name))
    tools = [...priority, ...rest].slice(0, provider.maxTools)
  }

  return tools
}

function resolveGroqReasoningEffort(req: any): 'low' | 'medium' | 'high' | undefined {
  const effort = req?.output_config?.effort
  if (effort === 'low' || effort === 'medium' || effort === 'high') {
    return effort
  }
  // Groq's reasoning_effort only accepts low/medium/high. Anthropic's
  // 'xhigh' and 'max' are super-set tiers — map them to the strongest
  // Groq tier so cross-provider fallback preserves user intent.
  if (effort === 'xhigh' || effort === 'max') {
    return 'high'
  }
  return undefined
}

function resolveDeepSeekThinking(
  req: any,
): { type: 'enabled' | 'disabled' } | undefined {
  const thinkingType = req?.thinking?.type
  if (thinkingType === 'enabled' || thinkingType === 'disabled') {
    return { type: thinkingType }
  }

  const effort = req?.output_config?.effort
  if (effort === 'low') {
    return { type: 'disabled' }
  }
  // DeepSeek exposes binary thinking, not graded effort — every level
  // at or above 'medium' (including 'xhigh' and 'max') maps to enabled.
  if (effort === 'medium' || effort === 'high' || effort === 'xhigh' || effort === 'max') {
    return { type: 'enabled' }
  }

  return undefined
}

// Models whose upstream API generates reasoning_content (DeepSeek-R1 shape)
// that counts against the response token budget — even when we suppress
// it from the wire via include_reasoning=false. They need the full
// provider.maxOutputTokens floor or visible content gets squeezed out.
// Distinct from provider.requiresReasoning, which ALSO triggers
// reasoning-content round-trip in chat_ctx (a DeepSeek-thinking-mode
// quirk these models don't share).
//
// Membership verified live 2026-05-27 with max_tokens=30 against
// /v1/messages → /v1/chat/completions. Models that burned the entire
// budget on hidden reasoning (finish_reason=length, completion_tokens
// equal to the cap, empty content) made the list. gpt-5.1 is notably
// NOT on it — it defaulted to minimal reasoning and returned visible
// text within 10 tokens. gemini-2.5-flash also stayed out.
function usesHiddenReasoning(provider: Provider): boolean {
  if (provider.model.includes('gpt-oss')) return true
  if (provider.name === 'kimi') return true
  // OpenAI GPT-5, GPT-5-mini, GPT-5-nano (exclude gpt-5.1 + later)
  if (provider.name === 'openai' && /^gpt-5(-mini|-nano)?$/.test(provider.model)) return true
  // Google Gemini 2.5 Pro (hidden thinking; the flash variants don't)
  if (provider.name === 'gemini' && provider.model.startsWith('gemini-2.5-pro')) return true
  return false
}

// OpenAI's GPT-5 family (gpt-5, gpt-5-mini, gpt-5-nano, gpt-5.1) ships a
// stricter request shape than the legacy chat-completions models:
//   - max_tokens → renamed to max_completion_tokens
//   - temperature → must be exactly 1 (only default is accepted)
// Detect by model-id prefix so future siblings (e.g. gpt-5.2) inherit.
function isGpt5Family(provider: Provider): boolean {
  return provider.name === 'openai' && provider.model.startsWith('gpt-5')
}

function applyProviderSpecificParams(out: any, req: any, provider: Provider): void {
  if (provider.name === 'deepseek') {
    const thinking = resolveDeepSeekThinking(req)
    if (thinking) {
      // DeepSeek exposes binary thinking control rather than graded effort.
      // We map explicit Jarvis effort choices onto that upstream switch.
      out.thinking = thinking
    }
  }

  if (provider.name === 'kimi') {
    // Moonshot's K2.6 endpoint rejects any temperature !== 1 with a
    // hard 400 "invalid temperature: only 1 is allowed for this model".
    // Pin regardless of what the client sent — matches DeepSeek-R1's
    // similar constraint pattern.
    out.temperature = 1
  }

  if (provider.name === 'groq') {
    // Route through the highest service tier the account is entitled to.
    // Without this, Groq silently buckets every request into `on_demand`
    // (the strictest TPM cap) even for Dev-tier accounts. `auto` = server
    // picks best available. Override via JARVIS_GROQ_TIER ("flex",
    // "on_demand", etc.) if you want to pin one explicitly.
    out.service_tier = process.env.JARVIS_GROQ_TIER ?? 'auto'

    if (provider.model.includes('gpt-oss')) {
      // Groq GPT-OSS exposes official reasoning_effort controls.
      // Hide provider-specific reasoning traces to keep the proxy response
      // aligned with the Anthropic-shaped UI expectations.
      out.include_reasoning = false

      const reasoningEffort = resolveGroqReasoningEffort(req)
      if (reasoningEffort) {
        out.reasoning_effort = reasoningEffort
      }
    }
  }
}

// ── Main conversion: Anthropic request → OpenAI request ───────────────────

export function convertRequest(req: any, provider: Provider): any {
  const systemText = extractSystemText(req.system)
  const messages: OpenAIMessage[] = []

  if (systemText) {
    messages.push({ role: 'system', content: systemText })
  }

  const converted = convertMessages(
    req.messages ?? [],
    provider.requiresReasoning,
    provider.supportsVision,
  )
  messages.push(...converted)

  const repairedMessages = repairMessageSequence(messages)

  const tools = req.tools && req.tools.length > 0
    ? convertTools(req.tools, provider)
    : undefined

  // For thinking-mode upstreams the client's max_tokens budget covers BOTH
  // reasoning_content and visible output, but Claude Code sets it assuming
  // visible-only. A long chain-of-thought then exhausts the cap and tool
  // arguments stream truncates mid-emission, landing with empty input.
  // Always grant the provider max for thinking models so reasoning has
  // headroom and tool args fit. Same floor for hidden-reasoning models
  // (gpt-oss-*, Kimi K2.6) — see usesHiddenReasoning above.
  const usesReasoningBudget = provider.requiresReasoning || usesHiddenReasoning(provider)
  const maxTokens = usesReasoningBudget
    ? provider.maxOutputTokens
    : Math.min(req.max_tokens ?? provider.maxOutputTokens, provider.maxOutputTokens)

  const gpt5 = isGpt5Family(provider)
  const out: any = {
    model: provider.model,
    messages: repairedMessages,
    // GPT-5 family rejects max_tokens entirely (use max_completion_tokens)
    // and only accepts temperature=1. Every other upstream still wants
    // the legacy shape — keep them on max_tokens + the client's temp.
    ...(gpt5
      ? { max_completion_tokens: maxTokens, temperature: 1 }
      : { max_tokens: maxTokens, temperature: req.temperature ?? 0.3 }),
    stream: req.stream ?? false,
  }

  if (tools && tools.length > 0) {
    out.tools = tools
    if (provider.supportsToolChoice) {
      out.tool_choice = req.tool_choice?.type === 'any' ? 'required' : 'auto'
    }
  }

  applyProviderSpecificParams(out, req, provider)

  return out
}

// ── Convert OpenAI non-streaming response → Anthropic response ────────────

export function convertResponse(openaiResp: any, model: string): any {
  const choice = openaiResp.choices?.[0]
  if (!choice) throw new Error('No choices in OpenAI response')

  const msg = choice.message
  const content: any[] = []

  if (msg.content) {
    // Qwen3 (and possibly future open-source models) emit their chain-
    // of-thought inside literal <think>...</think> blocks in the visible
    // content. Strip them on the wire so the CLI doesn't render the
    // reasoning as user-visible text.
    const text = modelLeaksThinkTags(model)
      ? stripThinkTags(msg.content)
      : msg.content
    if (text) content.push({ type: 'text', text })
  }

  if (msg.tool_calls) {
    for (const tc of msg.tool_calls) {
      let input: unknown
      try {
        input = JSON.parse(tc.function.arguments)
      } catch {
        input = { _raw: tc.function.arguments }
      }
      content.push({
        type: 'tool_use',
        id: tc.id,
        name: tc.function.name,
        input,
      })
      if (msg.reasoning_content) {
        setReasoning(tc.id, msg.reasoning_content)
      }
    }
  }

  const stopReason = choice.finish_reason === 'tool_calls' ? 'tool_use'
    : choice.finish_reason === 'stop' ? 'end_turn'
    : choice.finish_reason === 'length' ? 'max_tokens'
    : 'end_turn'

  return {
    id: openaiResp.id ?? 'msg_proxy',
    type: 'message',
    role: 'assistant',
    model,
    content,
    stop_reason: stopReason,
    stop_sequence: null,
    usage: (() => {
      // DeepSeek returns `prompt_cache_hit_tokens` for the subset of
      // input tokens served from cache (billed at the cheaper rate).
      // Map that → Anthropic `cache_read_input_tokens` and subtract
      // from `input_tokens` so the CLI cost-tracker bills cache hits
      // at the cache-read rate, not the full input rate. Groq has no
      // cache field; cacheHit stays 0 and input_tokens equals
      // prompt_tokens.
      const promptTokens = openaiResp.usage?.prompt_tokens ?? 0
      const cacheHit = openaiResp.usage?.prompt_cache_hit_tokens ?? 0
      return {
        input_tokens: Math.max(0, promptTokens - cacheHit),
        output_tokens: openaiResp.usage?.completion_tokens ?? 0,
        cache_read_input_tokens: cacheHit,
      }
    })(),
  }
}
