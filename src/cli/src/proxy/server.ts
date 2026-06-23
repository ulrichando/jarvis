import { convertRequest, convertResponse, clampRequestForProvider } from './convert.js'
import { convertOpenAIStreamToAnthropic, type StreamStats } from './stream.js'
import { getProvider, getProviderForModel, type Provider } from './providers.js'
import { fetchWithRetry } from './retry.js'
import { forwardAnthropicNative } from './anthropicPassthrough.js'
import { logDeepseekCacheStats, logRequest, newRequestId, type RequestLog } from './logger.js'
import {
  buildSyntheticWebSearchResponse,
  extractWebSearchQuery,
  searchDuckDuckGo,
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

type AttemptOutcome = {
  response: Response | null
  errorMessage: string | null
  retriesUsed: number
  ttfbMs: number | null
  provider: Provider
  fallbackUsed: boolean
  primaryError: string | null
}

async function executeWithFallback(
  primary: Provider,
  openaiReq: any,
): Promise<AttemptOutcome> {
  const chain: Provider[] = [primary]
  for (const fallbackId of primary.fallback) {
    const fp = getProviderForModel(fallbackId)
    if (fp) chain.push(fp)
  }

  let primaryError: string | null = null
  for (let i = 0; i < chain.length; i++) {
    const provider = chain[i]
    // Re-shape the request for this provider: clamp max_tokens to its cap,
    // truncate tools to its maxTools, and use the correct token-field name
    // for its family. Without this, a primary-shaped body (e.g. deepseek
    // max_tokens=65536, 25+ tools) sent verbatim to a fallback with a lower
    // cap (e.g. groq max_tokens=32768, maxTools=20) would 400 immediately,
    // defeating the fallback chain exactly when it needs to fire.
    const reqForThisProvider = clampRequestForProvider(
      { ...openaiReq, model: provider.model },
      provider,
    )
    const result = await fetchWithRetry(`${provider.baseUrl}/chat/completions`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${provider.apiKey}`,
      },
      body: JSON.stringify(reqForThisProvider),
    })

    if (result.response && result.response.ok) {
      return {
        response: result.response,
        errorMessage: null,
        retriesUsed: result.retriesUsed,
        ttfbMs: result.ttfbMs,
        provider,
        fallbackUsed: i > 0,
        primaryError,
      }
    }

    // Capture this provider's failure reason for logging/decision-making.
    let thisErr: string
    if (result.response) {
      // Non-OK status: read body for error text, then decide whether
      // to fall back. Only fall back for transient failures (5xx/429),
      // not for 4xx (the request itself is the problem).
      const status = result.response.status
      let body = ''
      try { body = await result.response.text() } catch {}
      thisErr = `HTTP ${status}: ${body.slice(0, 500)}`
      const transient = status === 408 || status === 429 || (status >= 500 && status <= 599)
      if (!transient) {
        // Fail fast — no fallback, return the error to caller.
        return {
          response: null,
          errorMessage: thisErr,
          retriesUsed: result.retriesUsed,
          ttfbMs: result.ttfbMs,
          provider,
          fallbackUsed: i > 0,
          primaryError,
        }
      }
    } else {
      thisErr = result.error?.message ?? 'unknown fetch error'
    }
    if (i === 0) primaryError = thisErr
    console.warn(
      `[jarvis-proxy] ${provider.name}/${provider.model} failed (${thisErr}); ` +
      (i + 1 < chain.length ? `falling back to ${chain[i + 1].name}/${chain[i + 1].model}` : 'no more fallbacks'),
    )
  }

  return {
    response: null,
    errorMessage: primaryError ?? 'all providers exhausted',
    retriesUsed: 0,
    ttfbMs: null,
    provider: chain[chain.length - 1],
    fallbackUsed: chain.length > 1,
    primaryError,
  }
}

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

    let hits: Awaited<ReturnType<typeof searchDuckDuckGo>> = []
    let failed = false
    try {
      hits = await searchDuckDuckGo(webSearchQuery)
    } catch (e) {
      console.error('[jarvis-proxy] DuckDuckGo search failed:', e)
      failed = true
    }
    finish({
      provider: 'web_search',
      upstream_model: 'duckduckgo',
      error_type: failed ? 'web_search_failed' : null,
    })
    return new Response(
      JSON.stringify(buildSyntheticWebSearchResponse(webSearchQuery, model, hits, failed)),
      { headers: { 'Content-Type': 'application/json' } },
    )
  }

  let primaryProvider: Provider
  try {
    primaryProvider = getProviderForModel(anthropicReq.model) ?? getProvider()
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
  if (primaryProvider.name === 'anthropic') {
    return forwardAnthropicNative({
      provider: primaryProvider,
      anthropicReq,
      incomingHeaders: req.headers,
      isStream,
      requestId,
      onFinish: finish,
      baseLog,
    })
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
    const errMsg = outcome.errorMessage ?? 'upstream unreachable'
    console.error(`[jarvis-proxy] [${requestId.slice(0, 8)}] all providers failed: ${errMsg}`)
    finish({
      status: 502,
      error_type: 'upstream_unreachable',
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
          const errorEvent = `event: error\ndata: ${JSON.stringify({ type: 'error', error: { type: 'api_error', message: errMsg } })}\n\n`
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
      JSON.stringify({ type: 'error', error: { message: errMsg, type: 'api_error' } }),
      { status: 502, headers: { 'Content-Type': 'application/json' } },
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
  const anthropicResp = convertResponse(openaiResp, provider.model)
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
