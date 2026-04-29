// Structured JSONL logging for jarvis-proxy. One line per completed request,
// appended to ~/.jarvis/proxy.log. Async writes (queued on next tick) so
// logging never blocks the response. Mirrors the schema most production
// LLM proxies use (LiteLLM, Helicone) so it's grep/jq-friendly.

import { appendFile } from 'node:fs/promises'
import { mkdirSync } from 'node:fs'
import { dirname } from 'node:path'
import { homedir } from 'node:os'
import { randomUUID } from 'node:crypto'

const LOG_PATH = `${homedir()}/.jarvis/proxy.log`

try {
  mkdirSync(dirname(LOG_PATH), { recursive: true })
} catch {}

export type RequestLog = {
  ts: string
  request_id: string
  path: string
  provider: string | null
  upstream_model: string | null
  client_model: string | null
  status: number
  error_type: string | null
  error_message: string | null
  latency_ms: number
  ttfb_ms: number | null
  input_tokens: number | null
  output_tokens: number | null
  cache_read_tokens: number | null
  retries_used: number
  fallback_used: boolean
  primary_provider_error: string | null
  stream: boolean
  stop_reason: string | null
}

export function newRequestId(): string {
  return randomUUID()
}

export function logRequest(entry: RequestLog): void {
  // Fire-and-forget: don't await, don't await rejection — logging must
  // never throw into the response path.
  const line = JSON.stringify(entry) + '\n'
  appendFile(LOG_PATH, line).catch((e) => {
    console.error('[jarvis-proxy] log write failed:', (e as Error).message)
  })
}
