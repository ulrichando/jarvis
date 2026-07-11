/**
 * Local last-resort LLM failover for the jarvis-proxy (Stage 2 of full
 * offline parity, 2026-07-10 — mirrors the voice agent's Stage 1
 * `JARVIS_LOCAL_LLM_FAILOVER` philosophy in providers/llm.py).
 *
 * When EVERY cloud rung of a request's fallback chain has failed with a
 * genuine-outage signal (connection/DNS/timeout error, or 5xx/408 from the
 * upstream), the proxy retries the same OpenAI-shaped request ONCE against
 * the local Ollama daemon (qwen2.5:7b by default). Cloud stays strictly
 * primary:
 *
 *   - The local endpoint is NEVER probed or contacted while any cloud rung
 *     is healthy — this module is only invoked from the chain-exhaustion
 *     path in execute.ts, so the online request path is byte-identical.
 *   - Definitive provider responses (400/401/403/404/402…) return early in
 *     executeWithFallback BEFORE this module is reached: a bad key or an
 *     out-of-credits account is a REAL error the user must see, not a
 *     reason to silently degrade to a 7B local model.
 *   - 429 anywhere in the chain proves the cloud is REACHABLE (it answered)
 *     — rate-limit/quota exhaustion also surfaces instead of rerouting.
 *     See cloudFailureAllowsLocal().
 *
 * Flag: JARVIS_LOCAL_LLM_FAILOVER — default ON; set 0/false/no/off to
 * disable. Knobs shared with Stage 1: JARVIS_LOCAL_LLM_MODEL (default
 * qwen2.5:7b) and JARVIS_LOCAL_LLM_URL (default = the registry's ollama
 * base, i.e. OLLAMA_BASE_URL or http://localhost:11434, + /v1).
 */
import { getProviderForModel, type Provider } from './providers.js'
import { clampRequestForProvider } from './convert.js'

const DEFAULT_LOCAL_MODEL = 'qwen2.5:7b'

export function localFailoverEnabled(
  env: NodeJS.ProcessEnv = process.env,
): boolean {
  const v = (env.JARVIS_LOCAL_LLM_FAILOVER ?? '').trim().toLowerCase()
  // Default ON — an unset flag arms the last-resort tail. Explicit opt-out.
  return !(v === '0' || v === 'false' || v === 'no' || v === 'off')
}

export function localFailoverModelTag(
  env: NodeJS.ProcessEnv = process.env,
): string {
  return (env.JARVIS_LOCAL_LLM_MODEL ?? '').trim() || DEFAULT_LOCAL_MODEL
}

/**
 * Whether a TRANSIENT cloud-rung failure still qualifies as a genuine
 * outage for local-failover purposes. `status === null` means the fetch
 * itself threw (connection refused / DNS / reset / timeout) — the clearest
 * outage signal there is. 408 and 5xx are upstream-side failures. 429 means
 * the provider is up and answering — that's quota/rate pressure, NOT an
 * outage, so it must surface to the user rather than silently rerouting to
 * a weaker local model. (Non-transient 4xx never reaches this check — the
 * chain fails fast on those before any fallback, local included.)
 */
export function cloudFailureAllowsLocal(status: number | null): boolean {
  if (status === null) return true
  return status === 408 || (status >= 500 && status <= 599)
}

/**
 * Build the local Ollama rung as a normal proxy Provider, reusing the model
 * registry's stateless `ollama:<tag>` synthesis (same plumbing the /model
 * picker uses) so clamping/token-field/tool-choice shaping all behave
 * exactly like a user-selected local model. Returns null when the failover
 * is disabled or construction fails — callers treat null as "no local
 * rung", leaving the cloud error to surface unchanged.
 */
export function buildLocalFailoverProvider(
  env: NodeJS.ProcessEnv = process.env,
): Provider | null {
  if (!localFailoverEnabled(env)) return null
  try {
    const tag = localFailoverModelTag(env)
    const provider = getProviderForModel(`ollama:${tag}`)
    if (!provider) return null
    const urlOverride = (env.JARVIS_LOCAL_LLM_URL ?? '').trim()
    if (urlOverride) {
      return { ...provider, baseUrl: urlOverride.replace(/\/+$/, '') }
    }
    return provider
  } catch (e) {
    // Don't log the error object — a provider-construction error can embed the
    // apiKey env value in its text and CodeQL taints the whole error (clear-
    // text logging of sensitive info). The failure itself is the signal.
    void e
    console.warn(
      `[jarvis-proxy] local failover provider construction failed; cloud error will surface unchanged`,
    )
    return null
  }
}

export type LocalFailoverAttempt = {
  response: Response
  provider: Provider
  ttfbMs: number
}

/**
 * Single-shot attempt against the local Ollama daemon. Deliberately NOT
 * fetchWithRetry: the daemon is on loopback — it is either there or not,
 * and retry backoff would only delay the (already-failed) request's error.
 * Any local failure → null, so the caller surfaces the ORIGINAL cloud
 * error (never mask a cloud outage with a confusing local error).
 */
export async function attemptLocalFailover(opts: {
  openaiReq: unknown
  /** Providers already tried — if ollama was among them, local already had
   *  its turn (e.g. user explicitly selected a local model); don't double. */
  triedProviders: readonly Pick<Provider, 'name'>[]
  env?: NodeJS.ProcessEnv
  fetchImpl?: typeof fetch
}): Promise<LocalFailoverAttempt | null> {
  const env = opts.env ?? process.env
  const fetchImpl = opts.fetchImpl ?? fetch
  if (opts.triedProviders.some((p) => p.name === 'ollama')) return null
  const provider = buildLocalFailoverProvider(env)
  if (!provider) return null

  const reqForLocal = clampRequestForProvider(
    { ...(opts.openaiReq as Record<string, unknown>), model: provider.model },
    provider,
  )
  const t0 = Date.now()
  try {
    const response = await fetchImpl(`${provider.baseUrl}/chat/completions`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${provider.apiKey}`,
      },
      body: JSON.stringify(reqForLocal),
    })
    if (response.ok) {
      console.warn(
        `[jarvis-proxy] cloud chain exhausted — LOCAL failover served by ${provider.model} (${provider.baseUrl})`,
      )
      return { response, provider, ttfbMs: Date.now() - t0 }
    }
    let body = ''
    try { body = await response.text() } catch {}
    console.warn(
      `[jarvis-proxy] local failover ${provider.model} unavailable (HTTP ${response.status}: ${body.slice(0, 200)}); surfacing the cloud error`,
    )
    return null
  } catch (e) {
    console.warn(
      `[jarvis-proxy] local failover ${provider.model} unreachable (${(e as Error).message}); surfacing the cloud error`,
    )
    return null
  }
}
