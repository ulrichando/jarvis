/**
 * Provider fallback-chain executor for the jarvis-proxy.
 *
 * Extracted verbatim from server.ts (2026-07-10, Stage 2 offline parity) so
 * the chain logic — including the new local last-resort tail — is unit-
 * testable without booting Bun.serve. Behavior with a healthy cloud chain
 * is unchanged: same rung order, same clamping, same fail-fast on
 * definitive 4xx. The ONLY addition is the chain-exhaustion tail: see
 * localFailover.ts.
 */
import { clampRequestForProvider } from './convert.js'
import { getProviderForModel, type Provider } from './providers.js'
import { fetchWithRetry } from './retry.js'
import {
  attemptLocalFailover,
  cloudFailureAllowsLocal,
} from './localFailover.js'
import { ensureOllamaNumCtx } from './ollamaContext.js'

export type AttemptOutcome = {
  response: Response | null
  errorMessage: string | null
  retriesUsed: number
  ttfbMs: number | null
  provider: Provider
  fallbackUsed: boolean
  primaryError: string | null
  // The upstream HTTP status when the failure was a definitive (non-transient)
  // provider response — 400/401/403/404/429/402 etc. Lets the caller relay the
  // REAL status to the CLI (e.g. "out of credits" 429, "bad key" 401) instead
  // of masking every failure as a generic 502 upstream_unreachable. Null when
  // the failure was a connection/transport error (no HTTP status).
  upstreamStatus?: number | null
}

/** Injectable dependencies — tests only. Production callers pass nothing. */
export type ExecuteDeps = {
  fetcher?: typeof fetchWithRetry
  localFetch?: typeof fetch
  env?: NodeJS.ProcessEnv
}

export async function executeWithFallback(
  primary: Provider,
  openaiReq: any,
  deps: ExecuteDeps = {},
): Promise<AttemptOutcome> {
  const fetcher = deps.fetcher ?? fetchWithRetry
  const chain: Provider[] = [primary]
  for (const fallbackId of primary.fallback) {
    // getProviderForModel THROWS when the fallback provider's API key env is
    // missing (the Provider builder validates the key). A broken fallback must
    // NOT block a healthy primary — skip it instead of letting the throw abort
    // the whole request before the primary is even attempted.
    try {
      const fp = getProviderForModel(fallbackId)
      if (fp) chain.push(fp)
    } catch (e) {
      // Log only the (non-sensitive) model id — NOT the error object: a
      // provider-construction error can carry the apiKey env value in its
      // text, and CodeQL taints the whole error (clear-text logging of
      // sensitive information). The failure itself is what matters here.
      void e
      console.warn(
        `[jarvis-proxy] fallback ${fallbackId} unavailable; skipping`,
      )
    }
  }

  let primaryError: string | null = null
  // Local last-resort eligibility (Stage 2 offline parity). Stays true only
  // while every cloud failure looks like a genuine outage (conn error /
  // 5xx / 408). A 429 anywhere proves the cloud answered — quota/rate
  // pressure must surface, not silently reroute to the local model.
  let localEligible = true
  for (let i = 0; i < chain.length; i++) {
    let provider = chain[i]
    // Ollama rungs (any position — user-selected primary, fallback rung, or
    // the anthropic-outage reroute) run against a context-bumped derived
    // model: Ollama's /v1 endpoint ignores num_ctx in the body, so without
    // this every real CLI request 400s at the daemon-default 4096 context.
    // CLI-request-specific by design — the voice-agent shares this daemon
    // and must keep the base tag at the default context (VRAM budget).
    // No-ops for cloud providers; falls back to the base tag on any failure.
    // See ollamaContext.ts for the full mechanism + JARVIS_OLLAMA_NUM_CTX.
    if (provider.name === 'ollama') {
      provider = await ensureOllamaNumCtx(provider, deps.localFetch, deps.env)
    }
    // Re-shape the request for this provider: clamp max_tokens to its cap,
    // truncate tools to its maxTools, and use the correct token-field name
    // for its family. Without this, a primary-shaped body (e.g. deepseek
    // max_tokens=65536, 25+ tools) sent verbatim to a fallback with a lower
    // cap (e.g. kimi max_tokens=32768, maxTools=16) would 400 immediately,
    // defeating the fallback chain exactly when it needs to fire.
    const reqForThisProvider = clampRequestForProvider(
      { ...openaiReq, model: provider.model },
      provider,
    )
    const result = await fetcher(`${provider.baseUrl}/chat/completions`, {
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
        // Fail fast — no fallback, return the error to caller. Carry the real
        // upstream status so the caller relays it (400/401/403/404/402) rather
        // than masking it as a generic 502. Definitive responses NEVER reach
        // the local tail either — a bad key / bad request is the user's
        // problem to see, not a reason to degrade to a local model.
        return {
          response: null,
          errorMessage: thisErr,
          retriesUsed: result.retriesUsed,
          ttfbMs: result.ttfbMs,
          provider,
          fallbackUsed: i > 0,
          primaryError,
          upstreamStatus: status,
        }
      }
      if (!cloudFailureAllowsLocal(status)) localEligible = false
    } else {
      thisErr = result.error?.message ?? 'unknown fetch error'
    }
    if (i === 0) primaryError = thisErr
    console.warn(
      `[jarvis-proxy] ${provider.name}/${provider.model} failed (${thisErr}); ` +
      (i + 1 < chain.length ? `falling back to ${chain[i + 1].name}/${chain[i + 1].model}` : 'no more fallbacks'),
    )
  }

  // Chain exhausted on outage-shaped failures only → LAST-resort local tail
  // (Stage 2 offline parity; see localFailover.ts). Reached exclusively when
  // every cloud rung already failed, so the online path never gets here.
  if (localEligible) {
    const local = await attemptLocalFailover({
      openaiReq,
      triedProviders: chain,
      env: deps.env,
      fetchImpl: deps.localFetch,
    })
    if (local) {
      return {
        response: local.response,
        errorMessage: null,
        retriesUsed: 0,
        ttfbMs: local.ttfbMs,
        provider: local.provider,
        fallbackUsed: true,
        primaryError,
      }
    }
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
