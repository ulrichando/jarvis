/**
 * Anthropic-native passthrough for the jarvis-proxy.
 *
 * Every other provider in the proxy (DeepSeek, Groq, Kimi, etc.) speaks
 * an OpenAI-compatible /chat/completions surface, so the proxy converts
 * the CLI's Anthropic-shape request → OpenAI shape on the way out and
 * back again on the way in. For Anthropic itself that round-trip is
 * the wrong direction: api.anthropic.com publishes /v1/messages in the
 * exact shape the CLI is already sending, and SSE events come back in
 * the exact shape the CLI's `@anthropic-ai/sdk` expects.
 *
 * This module short-circuits the conversion: forward the CLI's request
 * body byte-for-byte to {baseUrl}/messages, swap auth headers in, pipe
 * the response back unchanged. We also drop a few hop-by-hop headers
 * that would either leak proxy details or upset Anthropic's gateway
 * (content-length is recomputed by fetch; host is rewritten; authorization
 * is replaced with x-api-key).
 *
 * No cross-provider fallback on this path — if Anthropic 5xx's, we
 * surface that to the caller rather than try to translate the same
 * request to a DeepSeek-shaped fallback mid-stream. The registry
 * fallback chain assumes provider-shape parity, so it doesn't apply.
 */
import type { Provider } from './providers.js'
import type { RequestLog } from './logger.js'

const ANTHROPIC_VERSION =
  process.env.JARVIS_ANTHROPIC_VERSION ?? '2023-06-01'

/**
 * Headers we never forward to Anthropic. `host` is recomputed by the
 * fetch client; `content-length` is recomputed when we re-serialize the
 * body; `authorization` is replaced with `x-api-key`; the proxy's CORS
 * headers are local-only.
 */
const STRIPPED_HEADERS = new Set([
  'host',
  'content-length',
  'connection',
  'transfer-encoding',
  'authorization',
  'x-api-key',
])

function buildUpstreamHeaders(
  incoming: Headers,
  apiKey: string,
): Record<string, string> {
  const out: Record<string, string> = {
    'content-type':       'application/json',
    'x-api-key':          apiKey,
    'anthropic-version':  ANTHROPIC_VERSION,
  }
  // Forward any client-set anthropic-beta / anthropic-dangerous-* flags so
  // features the CLI enables (prompt caching, tool_use, output_config…)
  // make it to the upstream. The Anthropic SDK sets these from its own
  // beta-feature list; preserving them keeps every CLI-side toggle alive.
  incoming.forEach((value, key) => {
    const k = key.toLowerCase()
    if (STRIPPED_HEADERS.has(k)) return
    if (k.startsWith('anthropic-')) {
      out[k] = value
    }
    // Preserve the original User-Agent for telemetry alignment (it lets
    // Anthropic's dashboard tag the request as coming from the CLI shape
    // they expect). If you want to mask it, set JARVIS_PROXY_USER_AGENT.
    if (k === 'user-agent') {
      out['user-agent'] = process.env.JARVIS_PROXY_USER_AGENT ?? value
    }
  })
  return out
}

export type AnthropicPassthroughArgs = {
  provider: Provider
  anthropicReq: any
  incomingHeaders: Headers
  isStream: boolean
  requestId: string
  onFinish: (entry: Partial<RequestLog>) => void
  baseLog: RequestLog
}

export async function forwardAnthropicNative(
  args: AnthropicPassthroughArgs,
): Promise<Response> {
  const {
    provider,
    anthropicReq,
    incomingHeaders,
    isStream,
    requestId,
    onFinish,
  } = args

  // Re-target the request to the upstream model id resolved from the
  // jarvis model registry (in case the CLI sent a friendly alias and
  // the registry mapped it to a canonical name like 'claude-sonnet-4-6').
  const upstreamReq = { ...anthropicReq, model: provider.model }
  const url = `${provider.baseUrl}/messages`
  const headers = buildUpstreamHeaders(incomingHeaders, provider.apiKey)

  console.log(
    `[jarvis-proxy] [${requestId.slice(0, 8)}] CLI="${anthropicReq.model ?? '(default)'}" → ` +
    `${url} model="${provider.model}" stream=${isStream} (anthropic-native)`,
  )

  const tsStart = Date.now()
  let upstreamResp: Response
  try {
    upstreamResp = await fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify(upstreamReq),
    })
  } catch (e: any) {
    const errMsg = `anthropic unreachable: ${e?.message ?? e}`
    onFinish({
      status: 502,
      error_type: 'upstream_unreachable',
      error_message: errMsg,
    })
    if (isStream) {
      const enc = new TextEncoder()
      const errStream = new ReadableStream<Uint8Array>({
        start(controller) {
          const event = `event: error\ndata: ${JSON.stringify({
            type: 'error',
            error: { type: 'api_error', message: errMsg },
          })}\n\n`
          controller.enqueue(enc.encode(event))
          controller.close()
        },
      })
      return new Response(errStream, {
        headers: {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          'Connection': 'keep-alive',
        },
      })
    }
    return new Response(
      JSON.stringify({ type: 'error', error: { message: errMsg, type: 'api_error' } }),
      { status: 502, headers: { 'Content-Type': 'application/json' } },
    )
  }

  // Non-OK status: pass the upstream body through verbatim so the CLI
  // gets the exact error Anthropic returned (rate-limit, auth, etc.).
  if (!upstreamResp.ok) {
    let body = ''
    try { body = await upstreamResp.text() } catch {}
    onFinish({
      status: upstreamResp.status,
      error_type: upstreamResp.status >= 500 ? 'upstream_error' : 'upstream_4xx',
      error_message: `HTTP ${upstreamResp.status}: ${body.slice(0, 500)}`,
    })
    return new Response(body, {
      status: upstreamResp.status,
      headers: {
        'Content-Type': upstreamResp.headers.get('content-type') ?? 'application/json',
        'x-jarvis-request-id': requestId,
        'x-jarvis-provider': 'anthropic',
      },
    })
  }

  const ttfbMs = Date.now() - tsStart

  if (isStream) {
    // Anthropic's SSE shape already matches what the CLI expects, so we
    // pipe the body through unchanged. We do count tokens from the
    // `message_start` / `message_delta` events for telemetry parity,
    // but only as a side-effect — never block or transform.
    const reader = upstreamResp.body?.getReader()
    if (!reader) {
      onFinish({ status: 502, error_type: 'no_body', error_message: 'upstream returned no body' })
      return new Response('upstream returned no body', { status: 502 })
    }

    let inputTokens: number | null = null
    let outputTokens: number | null = null
    let cacheReadTokens: number | null = null
    let stopReason: string | null = null
    let buffer = ''

    const stream = new ReadableStream<Uint8Array>({
      async start(controller) {
        try {
          while (true) {
            const { done, value } = await reader.read()
            if (done) break
            controller.enqueue(value)
            // Best-effort telemetry parse — never throw, never block.
            try {
              buffer += new TextDecoder().decode(value, { stream: true })
              // Process complete SSE events (delimited by \n\n).
              let idx: number
              while ((idx = buffer.indexOf('\n\n')) !== -1) {
                const eventBlock = buffer.slice(0, idx)
                buffer = buffer.slice(idx + 2)
                const dataLine = eventBlock
                  .split('\n')
                  .find(l => l.startsWith('data:'))
                if (!dataLine) continue
                const payload = dataLine.slice(5).trim()
                if (!payload || payload === '[DONE]') continue
                let evt: any
                try { evt = JSON.parse(payload) } catch { continue }
                if (evt.type === 'message_start' && evt.message?.usage) {
                  inputTokens     = evt.message.usage.input_tokens ?? null
                  outputTokens    = evt.message.usage.output_tokens ?? null
                  cacheReadTokens = evt.message.usage.cache_read_input_tokens ?? null
                } else if (evt.type === 'message_delta') {
                  if (evt.usage?.output_tokens != null) {
                    outputTokens = evt.usage.output_tokens
                  }
                  if (evt.delta?.stop_reason) {
                    stopReason = evt.delta.stop_reason
                  }
                }
              }
            } catch { /* telemetry-only — swallow */ }
          }
        } catch (e: any) {
          console.error(`[jarvis-proxy] [${requestId.slice(0, 8)}] anthropic stream error:`, e)
          onFinish({
            error_type: 'stream_error',
            error_message: e?.message ?? String(e),
          })
        } finally {
          controller.close()
          onFinish({
            input_tokens: inputTokens,
            output_tokens: outputTokens,
            cache_read_tokens: cacheReadTokens,
            stop_reason: stopReason,
            ttfb_ms: ttfbMs,
          })
        }
      },
    })

    return new Response(stream, {
      headers: {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'x-jarvis-request-id': requestId,
        'x-jarvis-provider': 'anthropic',
        'x-jarvis-fallback-used': 'false',
      },
    })
  }

  // Non-streaming: response is already Anthropic-shape JSON, pass through.
  const body = await upstreamResp.text()
  let usage: any = null
  try {
    const parsed = JSON.parse(body)
    usage = parsed?.usage ?? null
    onFinish({
      input_tokens:      usage?.input_tokens ?? null,
      output_tokens:     usage?.output_tokens ?? null,
      cache_read_tokens: usage?.cache_read_input_tokens ?? null,
      stop_reason:       parsed?.stop_reason ?? null,
      ttfb_ms:           ttfbMs,
    })
  } catch {
    onFinish({ ttfb_ms: ttfbMs })
  }
  return new Response(body, {
    headers: {
      'Content-Type': 'application/json',
      'x-jarvis-request-id': requestId,
      'x-jarvis-provider': 'anthropic',
      'x-jarvis-fallback-used': 'false',
    },
  })
}
