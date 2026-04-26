import type { Provider } from './providers.js'

// ── OpenAI message types ───────────────────────────────────────────────────

type OpenAIMessage =
  | { role: 'system'; content: string }
  | { role: 'user'; content: string }
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

// ── Convert Anthropic messages → OpenAI messages ──────────────────────────

export function convertMessages(anthropicMessages: any[]): OpenAIMessage[] {
  const out: OpenAIMessage[] = []

  for (const msg of anthropicMessages) {
    if (msg.role === 'assistant') {
      const content = msg.content
      if (typeof content === 'string') {
        out.push({ role: 'assistant', content })
        continue
      }
      if (!Array.isArray(content)) continue

      const textBlocks = content.filter((b: any) => b.type === 'text')
      const toolUseBlocks = content.filter((b: any) => b.type === 'tool_use')
      // Concat thinking blocks back into reasoning_content. DeepSeek's
      // thinking-mode API rejects multi-turn requests if the prior
      // assistant turn omits this field (error: "The reasoning_content
      // in the thinking mode must be passed back to the API."). We
      // store it on the Anthropic side as a `thinking` block on
      // egress (convertResponse) and re-attach here on ingress.
      const thinkingBlocks = content.filter((b: any) => b.type === 'thinking')
      const reasoning = thinkingBlocks.map((b: any) => b.thinking ?? '').join('')
      const text = textBlocks.map((b: any) => b.text ?? '').join('') || null

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

      // Remaining text becomes a user message
      if (otherBlocks.length > 0) {
        const text = otherBlocks
          .filter((b: any) => b.type === 'text')
          .map((b: any) => b.text ?? '')
          .join('')
        if (text) out.push({ role: 'user', content: text })
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
  if (effort === 'medium' || effort === 'high') {
    return { type: 'enabled' }
  }

  return undefined
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

  const converted = convertMessages(req.messages ?? [])
  messages.push(...converted)

  const repairedMessages = repairMessageSequence(messages)

  const tools = req.tools && req.tools.length > 0
    ? convertTools(req.tools, provider)
    : undefined

  const maxTokens = Math.min(req.max_tokens ?? provider.maxOutputTokens, provider.maxOutputTokens)

  const out: any = {
    model: provider.model,
    messages: repairedMessages,
    max_tokens: maxTokens,
    temperature: req.temperature ?? 0.3,
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

  // Thinking block FIRST so the round-trip through Anthropic schema
  // preserves DeepSeek's reasoning_content. convertMessages will
  // re-extract it on the next turn. Empty signature is fine — we
  // control both sides of this round trip; Anthropic-style signature
  // verification doesn't run against the proxy.
  if (msg.reasoning_content) {
    content.push({
      type: 'thinking',
      thinking: msg.reasoning_content,
      signature: '',
    })
  }

  if (msg.content) {
    content.push({ type: 'text', text: msg.content })
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
    usage: {
      input_tokens: openaiResp.usage?.prompt_tokens ?? 0,
      output_tokens: openaiResp.usage?.completion_tokens ?? 0,
    },
  }
}
