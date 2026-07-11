import { convertRequest, convertResponse } from './convert.js'
import { convertOpenAIStreamToAnthropic, type StreamStats } from './stream.js'
import { getProvider, getProviderForModel, type Provider } from './providers.js'
import { executeWithFallback } from './execute.js'
import { buildLocalFailoverProvider } from './localFailover.js'
import { forwardAnthropicNative } from './anthropicPassthrough.js'
import { logDeepseekCacheStats, logRequest, newRequestId, type RequestLog } from './logger.js'
import { classifyChatCompletionsRequest, buildHubConfig } from './hubGateway.js'
import {
  buildSyntheticWebSearchResponse,
  extractWebSearchQuery,
  webSearch,
  writeSyntheticWebSearchStream,
} from './webSearch.js'
import { verifyProxyToken } from './proxyJwt.js'
import { readKeysEnvValue } from '../utils/jarvisKeysEnv.js'

const PORT = parseInt(process.env.JARVIS_PROXY_PORT ?? '4000')

console.log(`[jarvis-proxy] Starting on port ${PORT}`)

// Last-resort process guards. A single bad request — or, most commonly, a
// client that disconnects mid-stream so an unguarded controller.enqueue()
// throws inside a stream `finally` — must NEVER take the whole proxy down.
// Bun terminates the process on an unhandled rejection/exception by default,
// which is exactly how the proxy silently vanished mid-session (no stderr, no
// log, after hours of clean traffic). Each request is independent, so log the
// fault and keep serving; the next request is unaffected. This is the durable
// cure regardless of which exact line threw — see the systemd unit for the
// out-of-process backstop.
process.on('uncaughtException', (err) => {
  console.error('[jarvis-proxy] uncaughtException (kept alive):', err)
})
process.on('unhandledRejection', (reason) => {
  console.error('[jarvis-proxy] unhandledRejection (kept alive):', reason)
})

// executeWithFallback + AttemptOutcome moved to execute.ts (2026-07-10,
// Stage 2 offline parity) so the chain — including its new local last-resort
// tail — is unit-testable without booting Bun.serve. Same behavior online.

// Safety guard: refuse to bind on a non-loopback interface unless the operator
// has explicitly opted in. Inbound auth is OFF by default (see AUTH_REQUIRED
// below); without it, exposing the proxy to the LAN would create an open
// key-spending LLM relay accessible to any host on the network. Default
// (127.0.0.1 / localhost / ::1) is always allowed; set JARVIS_ALLOW_PUBLIC_BIND=1
// to override for deliberate public deployments — pair that with `jarvis auth
// login` so AUTH_REQUIRED actually gates inbound requests.
const PROXY_HOST = process.env.JARVIS_PROXY_HOST ?? '127.0.0.1'
const LOOPBACK_HOSTS = new Set(['127.0.0.1', 'localhost', '::1'])
if (!LOOPBACK_HOSTS.has(PROXY_HOST) && process.env.JARVIS_ALLOW_PUBLIC_BIND !== '1') {
  console.error(
    `[jarvis-proxy] refusing non-loopback bind "${PROXY_HOST}" without JARVIS_ALLOW_PUBLIC_BIND=1 — ` +
    'the proxy has no inbound auth; binding to a public interface would expose an open LLM relay to the network.',
  )
  process.exit(1)
}

// ── Inbound auth ("OAuth via login") ────────────────────────────────────
// Optional, OFF by default. `jarvis auth login` provisions a per-user HS256
// token (JARVIS_PROXY_TOKEN), sets JARVIS_PROXY_AUTH_REQUIRED=1, and writes
// the shared verify secret (JARVIS_PROXY_JWT_SECRET) into ~/.jarvis/keys.env.
// When required, every /v1/messages request must carry a valid token, which
// we verify LOCALLY (no web-app round-trip) so the CLI keeps working when the
// web app is down. When NOT required, behavior is identical to the pre-auth
// proxy (loopback bind is still the only gate). The flag is read once at boot;
// `jarvis auth login` then a proxy restart (the launchers respawn it) flips it.
function isAuthEnvTruthy(v: string | undefined): boolean {
  return v === '1' || v === 'true' || v === 'yes'
}

const AUTH_REQUIRED =
  isAuthEnvTruthy(process.env.JARVIS_PROXY_AUTH_REQUIRED) ||
  isAuthEnvTruthy(readKeysEnvValue('JARVIS_PROXY_AUTH_REQUIRED'))

let cachedJwtSecret: string | null | undefined
function proxyJwtSecret(): string | null {
  const fromEnv = process.env.JARVIS_PROXY_JWT_SECRET?.trim()
  if (fromEnv) return fromEnv
  if (cachedJwtSecret !== undefined) return cachedJwtSecret
  cachedJwtSecret = readKeysEnvValue('JARVIS_PROXY_JWT_SECRET')?.trim() || null
  return cachedJwtSecret
}

function bearerCredential(headers: Headers): string | undefined {
  const authz = headers.get('authorization')
  if (authz && /^bearer /i.test(authz)) return authz.slice(7).trim()
  // x-api-key normally carries the SDK placeholder ('jarvis-proxy'); only
  // treat it as a credential when it is JWT-shaped (header.payload.sig).
  const xapi = headers.get('x-api-key')
  if (xapi && xapi.split('.').length === 3) return xapi.trim()
  return undefined
}

/** Rejection {status,message} if the request must be refused, else null. */
function checkInboundAuth(
  headers: Headers,
): { status: number; message: string } | null {
  if (!AUTH_REQUIRED) return null
  const secret = proxyJwtSecret()
  if (!secret) {
    // Enforcement on but no verify secret present — fail CLOSED. An
    // unverifiable "required" credential must never silently pass.
    return {
      status: 503,
      message: 'proxy auth required but JARVIS_PROXY_JWT_SECRET is unset',
    }
  }
  const token = bearerCredential(headers)
  if (!token) {
    return {
      status: 401,
      message: 'missing proxy credential — run `jarvis auth login`',
    }
  }
  const result = verifyProxyToken(token, secret)
  if (!result.ok) {
    return { status: 401, message: `invalid proxy credential: ${result.reason}` }
  }
  return null
}

const server = Bun.serve({
  port: PORT,
  hostname: PROXY_HOST,
  async fetch(req) {
    const url = new URL(req.url)

    if (url.pathname === '/health' || url.pathname === '/v1/health') {
      return new Response(JSON.stringify({ status: 'ok' }), {
        headers: { 'Content-Type': 'application/json' },
      })
    }

    if (req.method === 'POST' && (url.pathname.endsWith('/messages') || url.pathname === '/v1/messages')) {
      return handleMessagesRequest(req, url)
    }

    if (req.method === 'POST' &&
        (url.pathname.endsWith('/chat/completions') || url.pathname === '/v1/chat/completions')) {
      return handleChatCompletionsRequest(req, url)
    }

    if (req.method === 'GET' && (url.pathname === '/config' || url.pathname === '/hub/config')) {
      const authErr = checkInboundAuth(req.headers)
      if (authErr) {
        return new Response(
          JSON.stringify({ type: 'error', error: { type: 'authentication_error', message: authErr.message } }),
          { status: authErr.status, headers: { 'Content-Type': 'application/json' } })
      }
      return new Response(JSON.stringify(buildHubConfig()), {
        headers: { 'Content-Type': 'application/json' },
      })
    }

    return new Response('Not found', { status: 404 })
  },
  // Catch-all for anything thrown out of the request lifecycle or a socket
  // error. Without it, a throw escaping fetch() (or a low-level connection
  // error) can take the whole process down. Return a 500 instead of dying.
  error(err: Error) {
    console.error('[jarvis-proxy] Bun.serve error (kept alive):', err)
    return new Response(
      JSON.stringify({
        type: 'error',
        error: { type: 'api_error', message: 'internal proxy error' },
      }),
      { status: 500, headers: { 'Content-Type': 'application/json' } },
    )
  },
})

async function handleMessagesRequest(req: Request, url: URL): Promise<Response> {
  const requestId = newRequestId()
  const tsStart = Date.now()

  // Build a baseline log entry that gets specialized at completion.
  const baseLog: RequestLog = {
    ts: new Date().toISOString(),
    request_id: requestId,
    path: url.pathname,
    provider: null,
    upstream_model: null,
    client_model: null,
    status: 200,
    error_type: null,
    error_message: null,
    latency_ms: 0,
    ttfb_ms: null,
    input_tokens: null,
    output_tokens: null,
    cache_read_tokens: null,
    retries_used: 0,
    fallback_used: false,
    primary_provider_error: null,
    stream: false,
    stop_reason: null,
  }
  const finish = (entry: Partial<RequestLog>) => {
    logRequest({ ...baseLog, ...entry, latency_ms: Date.now() - tsStart })
  }

  // Inbound auth gate (see checkInboundAuth). Runs before the body parse so an
  // unauthorized caller costs nothing. /health is exempt — handled in the
  // top-level fetch() before reaching here. Reading headers does not consume
  // the body, so req.json() below still works.
  const authErr = checkInboundAuth(req.headers)
  if (authErr) {
    finish({
      status: authErr.status,
      error_type: 'unauthorized',
      error_message: authErr.message,
    })
    return new Response(
      JSON.stringify({
        type: 'error',
        error: { type: 'authentication_error', message: authErr.message },
      }),
      { status: authErr.status, headers: { 'Content-Type': 'application/json' } },
    )
  }

  let anthropicReq: any
  try {
    anthropicReq = await req.json()
  } catch {
    finish({ status: 400, error_type: 'invalid_request_error', error_message: 'invalid JSON' })
    return new Response(
      JSON.stringify({ error: { message: 'Invalid JSON', type: 'invalid_request_error' } }),
      { status: 400, headers: { 'Content-Type': 'application/json' } },
    )
  }

  baseLog.client_model = anthropicReq.model ?? null
  baseLog.stream = anthropicReq.stream === true
  const isStream = baseLog.stream

  // First-party web_search interception (unchanged behavior).
  const webSearchQuery = extractWebSearchQuery(anthropicReq)
  if (webSearchQuery !== null) {
    const model = anthropicReq.model ?? 'jarvis-web-search'
    console.log(`[jarvis-proxy] web_search intercept: "${webSearchQuery}"`)

    if (isStream) {
      const stream = new ReadableStream<Uint8Array>({
        async start(controller) {
          try {
            await writeSyntheticWebSearchStream(webSearchQuery, model, controller)
            finish({ provider: 'web_search', upstream_model: 'duckduckgo' })
          } catch (e) {
            console.error('[jarvis-proxy] web_search stream error:', e)
            finish({ provider: 'web_search', error_type: 'web_search_error', error_message: (e as Error).message })
          } finally {
            controller.close()
          }
        },
      })
      return new Response(stream, {
        headers: {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          'Connection': 'keep-alive',
        },
      })
    }

    let hits: Awaited<ReturnType<typeof webSearch>> = []
    let failed = false
    try {
      hits = await webSearch(webSearchQuery)
    } catch (e) {
      console.error('[jarvis-proxy] web search failed:', e)
      failed = true
    }
    finish({
      provider: 'web_search',
      upstream_model: (process.env.SEARXNG_URL ?? '').trim() ? 'searxng' : 'duckduckgo',
      error_type: failed ? 'web_search_failed' : null,
    })
    return new Response(
      JSON.stringify(buildSyntheticWebSearchResponse(webSearchQuery, model, hits, failed)),
      { headers: { 'Content-Type': 'application/json' } },
    )
  }

  let primaryProvider: Provider
  try {
    const matched = getProviderForModel(anthropicReq.model)
    // A client that NAMED a model we don't recognize gets silently routed to the
    // default provider/model — which can mask a typo or a stale/removed model id
    // (the request runs, just not on the model the caller asked for). Surface it
    // once per request so it's diagnosable, but only when a real name was given
    // (an empty/absent model legitimately means "use the default", no warning).
    if (!matched && anthropicReq.model && String(anthropicReq.model).trim()) {
      console.warn(
        `[jarvis-proxy] [${requestId.slice(0, 8)}] unknown model "${anthropicReq.model}" ` +
        `not in the registry — falling back to the default provider/model`,
      )
    }
    primaryProvider = matched ?? getProvider()
  } catch (e: any) {
    finish({ status: 400, error_type: 'invalid_request_error', error_message: e.message })
    return new Response(
      JSON.stringify({ error: { message: e.message, type: 'invalid_request_error' } }),
      { status: 400, headers: { 'Content-Type': 'application/json' } },
    )
  }

  baseLog.provider = primaryProvider.name
  baseLog.upstream_model = primaryProvider.model

  // Anthropic-native passthrough: Anthropic's /messages endpoint
  // already speaks the CLI's wire shape, so converting to OpenAI's
  // chat-completions would break the round-trip. Forward the request
  // body verbatim (with x-api-key auth swapped in) and stream the
  // SSE response back unchanged. See anthropicPassthrough.ts for the
  // full implementation. No cross-provider fallback on this path —
  // if Anthropic is down we surface the error rather than translate
  // mid-request (the registry's fallback chain assumes shape parity).
  //
  // ONE exception (Stage 2 offline parity, 2026-07-10): on a genuine
  // OUTAGE (fetch threw / upstream 5xx — never 4xx/429, those pass
  // through verbatim), when the local failover is armed, the passthrough
  // returns an outage signal instead of the error and we re-route the
  // SAME request through the standard convert path against local Ollama.
  // With the failover disabled (or Anthropic healthy) this branch is
  // byte-identical to the pre-Stage-2 passthrough.
  let anthropicOutageError: string | null = null
  if (primaryProvider.name === 'anthropic') {
    const localProvider = buildLocalFailoverProvider()
    const passthrough = await forwardAnthropicNative({
      provider: primaryProvider,
      anthropicReq,
      incomingHeaders: req.headers,
      isStream,
      requestId,
      onFinish: finish,
      baseLog,
      signalOutage: localProvider != null,
    })
    if (passthrough instanceof Response) return passthrough
    anthropicOutageError = passthrough.outage.errMsg
    console.warn(
      `[jarvis-proxy] [${requestId.slice(0, 8)}] anthropic outage (${anthropicOutageError}); ` +
      `attempting LOCAL failover via ${localProvider!.model}`,
    )
    primaryProvider = localProvider!
    baseLog.provider = primaryProvider.name
    baseLog.upstream_model = primaryProvider.model
  }

  let openaiReq: any
  try {
    openaiReq = convertRequest(anthropicReq, primaryProvider)
  } catch (e: any) {
    console.error('[jarvis-proxy] Conversion error:', e)
    finish({ status: 400, error_type: 'invalid_request_error', error_message: e.message })
    return new Response(
      JSON.stringify({ error: { message: e.message, type: 'invalid_request_error' } }),
      { status: 400, headers: { 'Content-Type': 'application/json' } },
    )
  }

  console.log(
    `[jarvis-proxy] [${requestId.slice(0, 8)}] CLI="${baseLog.client_model ?? '(default)'}" → ` +
    `${primaryProvider.baseUrl}/chat/completions model="${primaryProvider.model}" stream=${isStream}`,
  )

  const outcome = await executeWithFallback(primaryProvider, openaiReq)

  if (!outcome.response) {
    // Anthropic-outage reroute where the local rung ALSO failed: surface the
    // ORIGINAL cloud failure (the user's provider) with the local failure as
    // context, instead of a bare confusing ollama error.
    if (anthropicOutageError) {
      outcome.errorMessage =
        `anthropic unreachable (${anthropicOutageError}); ` +
        `local failover also failed: ${outcome.errorMessage ?? 'unknown'}`
    }
    const errMsg = outcome.errorMessage ?? 'upstream unreachable'
    // Relay the REAL upstream status when the failure was a definitive provider
    // response (out-of-credits/bad-key/bad-request), so the CLI's error
    // classifier can say "out of credits" / "invalid key" instead of treating a
    // 4xx as a gateway failure. Genuine connection/exhaustion failures → 502.
    const status = outcome.upstreamStatus ?? 502
    const errorType =
      status === 401 ? 'authentication_error'
      : status === 403 ? 'permission_error'
      : status === 404 ? 'not_found_error'
      : status === 429 ? 'rate_limit_error'
      : status === 400 ? 'invalid_request_error'
      : status >= 400 && status < 500 ? 'invalid_request_error'
      : 'upstream_unreachable'
    console.error(`[jarvis-proxy] [${requestId.slice(0, 8)}] all providers failed (status ${status}): ${errMsg}`)
    finish({
      status,
      error_type: errorType,
      error_message: errMsg,
      retries_used: outcome.retriesUsed,
      fallback_used: outcome.fallbackUsed,
      primary_provider_error: outcome.primaryError,
      provider: outcome.provider.name,
      upstream_model: outcome.provider.model,
    })

    if (isStream) {
      const enc = new TextEncoder()
      const errStream = new ReadableStream<Uint8Array>({
        start(controller) {
          const errorEvent = `event: error\ndata: ${JSON.stringify({ type: 'error', error: { type: errorType, message: errMsg } })}\n\n`
          controller.enqueue(enc.encode(errorEvent))
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
      JSON.stringify({ type: 'error', error: { message: errMsg, type: errorType } }),
      { status, headers: { 'Content-Type': 'application/json' } },
    )
  }

  // outcome.response is OK — proceed with streaming or non-streaming dispatch.
  const provider = outcome.provider
  const providerResp = outcome.response

  baseLog.provider = provider.name
  baseLog.upstream_model = provider.model
  baseLog.retries_used = outcome.retriesUsed
  baseLog.fallback_used = outcome.fallbackUsed
  baseLog.primary_provider_error = outcome.primaryError
  baseLog.ttfb_ms = outcome.ttfbMs

  if (isStream) {
    const stream = new ReadableStream<Uint8Array>({
      async start(controller) {
        let stats: StreamStats | null = null
        try {
          stats = await convertOpenAIStreamToAnthropic(providerResp, provider.model, controller)
        } catch (e) {
          console.error(`[jarvis-proxy] [${requestId.slice(0, 8)}] stream error:`, e)
          finish({
            status: 200,
            error_type: 'stream_error',
            error_message: (e as Error).message,
          })
        } finally {
          controller.close()
          if (stats) {
            // DeepSeek cache observability (Goal B). Miss is derived
            // (stream's StreamStats only carries the hit count, but
            // inputTokens is the raw prompt total → miss = total - hit).
            if (provider.name === 'deepseek') {
              const hit = stats.cacheReadTokens
              const miss = Math.max(0, stats.inputTokens - hit)
              if (hit + miss > 0) {
                logDeepseekCacheStats(requestId, hit, miss)
              }
            }
            finish({
              input_tokens: stats.inputTokens,
              output_tokens: stats.outputTokens,
              cache_read_tokens: stats.cacheReadTokens,
              stop_reason: stats.stopReason,
            })
          }
        }
      },
    })
    return new Response(stream, {
      headers: {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'x-jarvis-request-id': requestId,
        'x-jarvis-provider': provider.name,
        'x-jarvis-fallback-used': String(outcome.fallbackUsed),
      },
    })
  }

  const rawText = await providerResp.text()
  let openaiResp: any
  try {
    openaiResp = JSON.parse(rawText)
  } catch (e) {
    finish({
      status: 502,
      error_type: 'upstream_parse_error',
      error_message: `Non-JSON upstream response (${rawText.length} bytes): ${rawText.slice(0, 120)}`,
    })
    return new Response(
      JSON.stringify({ type: 'error', error: { type: 'api_error', message: 'Upstream returned non-JSON response' } }),
      { status: 502, headers: { 'Content-Type': 'application/json', 'x-jarvis-request-id': requestId } },
    )
  }
  // A 200 with no usable choices/message (content filter, provider hiccup,
  // some rate-limit shapes) makes convertResponse throw ("No choices …"). Catch
  // it and return a clear error with a body preview instead of an opaque 500.
  let anthropicResp: any
  try {
    anthropicResp = convertResponse(openaiResp, provider.model)
  } catch (e) {
    finish({
      status: 502,
      error_type: 'upstream_no_choices',
      error_message: `Upstream 200 but unusable (${(e as Error).message}): ${rawText.slice(0, 120)}`,
    })
    return new Response(
      JSON.stringify({ type: 'error', error: { type: 'api_error', message: `Upstream returned no usable response: ${(e as Error).message}` } }),
      { status: 502, headers: { 'Content-Type': 'application/json', 'x-jarvis-request-id': requestId } },
    )
  }
  // DeepSeek cache observability (Goal B). Hit/miss come straight from
  // the upstream usage block — no derivation needed on the batch path.
  if (provider.name === 'deepseek' && openaiResp?.usage) {
    const hit = openaiResp.usage.prompt_cache_hit_tokens ?? 0
    const miss = openaiResp.usage.prompt_cache_miss_tokens ?? 0
    if (hit + miss > 0) {
      logDeepseekCacheStats(requestId, hit, miss)
    }
  }
  finish({
    input_tokens: openaiResp?.usage?.prompt_tokens ?? null,
    output_tokens: openaiResp?.usage?.completion_tokens ?? null,
    cache_read_tokens: openaiResp?.usage?.prompt_cache_hit_tokens ?? null,
    stop_reason: openaiResp?.choices?.[0]?.finish_reason ?? null,
  })
  return new Response(JSON.stringify(anthropicResp), {
    headers: {
      'Content-Type': 'application/json',
      'x-jarvis-request-id': requestId,
      'x-jarvis-provider': provider.name,
      'x-jarvis-fallback-used': String(outcome.fallbackUsed),
    },
  })
}

// OpenAI-shaped ingress. The proxy's upstream call is already
// `<baseUrl>/chat/completions`, so OpenAI-shape in → OpenAI-shape out is a pure
// PASSTHROUGH — no Anthropic<->OpenAI conversion (that's only the /v1/messages
// path). Reuses the same auth gate, fallback chain, and request logger.
async function handleChatCompletionsRequest(req: Request, url: URL): Promise<Response> {
  const requestId = newRequestId()
  const tsStart = Date.now()
  const baseLog: RequestLog = {
    ts: new Date().toISOString(), request_id: requestId, path: url.pathname,
    provider: null, upstream_model: null, client_model: null, status: 200,
    error_type: null, error_message: null, latency_ms: 0, ttfb_ms: null,
    input_tokens: null, output_tokens: null, cache_read_tokens: null,
    retries_used: 0, fallback_used: false, primary_provider_error: null,
    stream: false, stop_reason: null,
  }
  const finish = (entry: Partial<RequestLog>) =>
    logRequest({ ...baseLog, ...entry, latency_ms: Date.now() - tsStart })

  const authErr = checkInboundAuth(req.headers)
  if (authErr) {
    finish({ status: authErr.status, error_type: 'unauthorized', error_message: authErr.message })
    return new Response(
      JSON.stringify({ type: 'error', error: { type: 'authentication_error', message: authErr.message } }),
      { status: authErr.status, headers: { 'Content-Type': 'application/json' } })
  }

  let openaiReq: any
  try { openaiReq = await req.json() } catch {
    finish({ status: 400, error_type: 'invalid_request_error', error_message: 'invalid JSON' })
    return new Response(JSON.stringify({ error: { message: 'Invalid JSON', type: 'invalid_request_error' } }),
      { status: 400, headers: { 'Content-Type': 'application/json' } })
  }

  baseLog.client_model = openaiReq.model ?? null
  const isStream = openaiReq.stream === true
  baseLog.stream = isStream

  const route = classifyChatCompletionsRequest(openaiReq.model)
  if (route.kind === 'reject') {
    finish({ status: route.status, error_type: 'invalid_request_error', error_message: route.message })
    return new Response(JSON.stringify({ error: { message: route.message, type: 'invalid_request_error' } }),
      { status: route.status, headers: { 'Content-Type': 'application/json' } })
  }
  baseLog.provider = route.provider.name
  baseLog.upstream_model = route.provider.model

  const outcome = await executeWithFallback(route.provider, openaiReq)
  if (!outcome.response) {
    const errMsg = outcome.errorMessage ?? 'upstream unreachable'
    finish({ status: 502, error_type: 'upstream_unreachable', error_message: errMsg,
      retries_used: outcome.retriesUsed, fallback_used: outcome.fallbackUsed,
      primary_provider_error: outcome.primaryError, provider: outcome.provider.name,
      upstream_model: outcome.provider.model })
    return new Response(JSON.stringify({ error: { message: errMsg, type: 'api_error' } }),
      { status: 502, headers: { 'Content-Type': 'application/json' } })
  }

  baseLog.retries_used = outcome.retriesUsed
  baseLog.fallback_used = outcome.fallbackUsed
  baseLog.primary_provider_error = outcome.primaryError
  baseLog.ttfb_ms = outcome.ttfbMs
  const stdHeaders = {
    'x-jarvis-request-id': requestId,
    'x-jarvis-provider': outcome.provider.name,
    'x-jarvis-fallback-used': String(outcome.fallbackUsed),
  }

  if (isStream) {
    // ponytail: per-token logging omitted on the stream passthrough — would
    // require teeing the stream to parse the trailing `usage` chunk. Provider /
    // status / latency are still logged via the flush hook. Upgrade path: tee +
    // parse usage if stream token accounting is needed.
    if (!outcome.response.body) {
      finish({ status: 502, error_type: 'upstream_no_body', error_message: 'upstream returned no stream body' })
      return new Response(
        JSON.stringify({ error: { message: 'upstream returned no stream body', type: 'api_error' } }),
        { status: 502, headers: { 'Content-Type': 'application/json', ...stdHeaders } })
    }
    const logged = outcome.response.body.pipeThrough(new TransformStream<Uint8Array, Uint8Array>({
      flush() { finish({ provider: outcome.provider.name, upstream_model: outcome.provider.model }) },
    }))
    return new Response(logged, {
      headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache',
        'Connection': 'keep-alive', ...stdHeaders },
    })
  }

  const rawText = await outcome.response.text()
  let usage: any
  try { usage = JSON.parse(rawText)?.usage } catch {}
  finish({ input_tokens: usage?.prompt_tokens ?? null, output_tokens: usage?.completion_tokens ?? null })
  return new Response(rawText, { headers: { 'Content-Type': 'application/json', ...stdHeaders } })
}

// Preflight: build the default provider at boot so missing env fails loud
// here instead of returning 401s on the first real request.
try {
  const p = getProvider()
  console.log(`[jarvis-proxy] Ready — provider: ${p.name} (${p.baseUrl})`)
  console.log(
    `[jarvis-proxy] inbound auth: ${AUTH_REQUIRED ? 'REQUIRED (login token)' : 'open (loopback only)'}`,
  )
} catch (e: any) {
  console.error(`[jarvis-proxy] FATAL: ${e?.message ?? e}`)
  process.exit(1)
}
