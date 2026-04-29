// Retry-with-backoff for upstream LLM provider fetches. Conservative policy:
// retry only on signals that are reasonably attributable to transient
// upstream issues — never on 4xx (the request itself is the problem).
//
// Patterns mirror what production LLM proxies (LiteLLM, Portkey) do by
// default, with jitter to avoid retry storms when many requests retry in
// lockstep against a recovering provider.

const MAX_RETRIES = 3
const BASE_DELAY_MS = 500
const MAX_DELAY_MS = 8000

export type FetchAttemptResult = {
  response: Response | null
  error: Error | null
  retriesUsed: number
  ttfbMs: number | null
}

function backoffDelay(attempt: number, retryAfterHeader?: string | null): number {
  if (retryAfterHeader) {
    const seconds = parseInt(retryAfterHeader, 10)
    if (Number.isFinite(seconds) && seconds > 0) {
      return Math.min(seconds * 1000, MAX_DELAY_MS)
    }
  }
  const exp = Math.min(BASE_DELAY_MS * Math.pow(2, attempt), MAX_DELAY_MS)
  // Full jitter: random in [0, exp]. Avoids thundering-herd on recovery.
  return Math.floor(Math.random() * exp)
}

function isRetryableStatus(status: number): boolean {
  // 408 Request Timeout, 429 Too Many Requests, 5xx — all server-side
  // hints that retrying is reasonable. 4xx (other than 408/429) means
  // the request is malformed and retrying changes nothing.
  return status === 408 || status === 429 || (status >= 500 && status <= 599)
}

function isRetryableError(err: unknown): boolean {
  // Network-level failures: connection reset, DNS failure, TLS handshake
  // errors. fetch() throws TypeError with a cause for these in Bun/Node.
  if (!(err instanceof Error)) return false
  const msg = err.message.toLowerCase()
  return (
    msg.includes('econnreset') ||
    msg.includes('etimedout') ||
    msg.includes('econnrefused') ||
    msg.includes('enotfound') ||
    msg.includes('socket hang up') ||
    msg.includes('fetch failed') ||
    msg.includes('network')
  )
}

export async function fetchWithRetry(
  url: string,
  init: RequestInit,
): Promise<FetchAttemptResult> {
  const start = Date.now()
  let lastError: Error | null = null

  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    try {
      const response = await fetch(url, init)
      if (response.ok) {
        return {
          response,
          error: null,
          retriesUsed: attempt,
          ttfbMs: Date.now() - start,
        }
      }
      // Non-OK: decide whether to retry. We must consume the body before
      // discarding so the connection returns to the pool cleanly.
      if (isRetryableStatus(response.status) && attempt < MAX_RETRIES) {
        const retryAfter = response.headers.get('retry-after')
        try { await response.text() } catch {}
        await sleep(backoffDelay(attempt, retryAfter))
        lastError = new Error(`HTTP ${response.status} (attempt ${attempt + 1})`)
        continue
      }
      // Non-retryable status (4xx) or out of attempts — return as-is so
      // caller can format the error response with the upstream body.
      return {
        response,
        error: null,
        retriesUsed: attempt,
        ttfbMs: Date.now() - start,
      }
    } catch (e) {
      lastError = e as Error
      if (isRetryableError(e) && attempt < MAX_RETRIES) {
        await sleep(backoffDelay(attempt))
        continue
      }
      return {
        response: null,
        error: lastError,
        retriesUsed: attempt,
        ttfbMs: null,
      }
    }
  }

  return {
    response: null,
    error: lastError ?? new Error('all retry attempts exhausted'),
    retriesUsed: MAX_RETRIES,
    ttfbMs: null,
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}
