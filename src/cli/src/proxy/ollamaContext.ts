/**
 * CLI-request-specific num_ctx for local Ollama models (P0 of the
 * local-model-usable fix, 2026-07-11).
 *
 * Problem: the proxy talks to Ollama over its OpenAI-compat /v1 endpoint,
 * which IGNORES `num_ctx` / `options.num_ctx` in the request body (verified
 * live on Ollama 0.30.9). A daemon-default model therefore loads at
 * OLLAMA_CONTEXT_LENGTH (4096 unless overridden), and any real CLI request
 * (system prompt + tool schemas) 400s with "request (N tokens) exceeds the
 * available context size". Setting OLLAMA_CONTEXT_LENGTH globally on the
 * daemon is NOT an option: the same daemon serves the voice-agent's local
 * mode, and a global context bump re-creates the voice-STT VRAM OOM that was
 * fixed on the 6 GB box.
 *
 * Mechanism (chosen over rewriting the ollama execution path onto the native
 * /api/chat endpoint, which honors options.num_ctx but has a different
 * request/response/SSE shape and would collide with concurrent proxy work):
 * lazily `POST /api/create` a derived model manifest from the base tag with
 * `PARAMETER num_ctx <N>` baked in — e.g.
 *
 *   qwen3:4b-instruct-2507-q4_K_M  →  qwen3-4b-instruct-2507-q4_K_M-jarvis-ctx:32768
 *
 * and route the CLI's request at the derived name. Creation is a
 * manifest-only operation (weights are shared blobs — no disk/VRAM
 * duplication) and was verified live: a /v1 chat against the derived model
 * loads with CONTEXT=<N> in `ollama ps`. The voice-agent keeps using the
 * base tag at the daemon default, so this stays strictly CLI-request-specific.
 *
 * Knob: JARVIS_OLLAMA_NUM_CTX (read by the PROXY process).
 *   - unset            → 32768 (qwen3:4b's native context; the user's pick)
 *   - a positive int   → that context size
 *   - 0 / off / false  → disable derived routing entirely (base tag,
 *                        daemon-default context — pre-fix behavior)
 *
 * VRAM calibration (6 GB RTX 2060, measured 2026-07-11): 4096 ctx ≈ 3.2 GB
 * (100% GPU) · 6144 ≈ 3.6 GB (100% GPU) · 12288 ≈ 6.6 GB (43%/57% CPU/GPU
 * with the voice STT model resident) · 16384 ≈ 7.0 GB (~half CPU) · 32768
 * ≈ 10 GB (mostly CPU, slow). So the 32768 default trades speed for "never
 * 400s"; lower JARVIS_OLLAMA_NUM_CTX (e.g. 8192/12288) for GPU-resident
 * speed, or set OLLAMA_KV_CACHE_TYPE=q8_0 + OLLAMA_FLASH_ATTENTION=1 on the
 * daemon (user setting, halves KV cache) to keep more of a big context
 * on-GPU. The runtime log line below keeps this knob visible.
 */
import {
  OLLAMA_JARVIS_CTX_MARKER,
  isJarvisCtxDerivedTag,
} from '../utils/model/jarvisModelRegistry.js'
import type { Provider } from './providers.js'

export const DEFAULT_OLLAMA_NUM_CTX = 32768

/** Values that disable the derived-model routing entirely. */
const DISABLE_VALUES = new Set(['0', 'off', 'false', 'no'])

export function ollamaNumCtxDisabled(
  env: NodeJS.ProcessEnv = process.env,
): boolean {
  const raw = (env.JARVIS_OLLAMA_NUM_CTX ?? '').trim().toLowerCase()
  return DISABLE_VALUES.has(raw)
}

export function resolveOllamaNumCtx(
  env: NodeJS.ProcessEnv = process.env,
): number {
  const raw = (env.JARVIS_OLLAMA_NUM_CTX ?? '').trim()
  if (!raw) return DEFAULT_OLLAMA_NUM_CTX
  const parsed = Number.parseInt(raw, 10)
  // Anything unparseable or absurdly small falls back to the default —
  // a sub-2048 context can't fit even the lean local prompt + a reply.
  if (!Number.isFinite(parsed) || parsed < 2048) return DEFAULT_OLLAMA_NUM_CTX
  return parsed
}

/** `qwen3:4b-…` → `qwen3-4b-…-jarvis-ctx:<numCtx>` (Ollama names allow
 *  [A-Za-z0-9._-]; ':' and '/' from the base tag are flattened to '-'). */
export function derivedOllamaModelName(baseTag: string, numCtx: number): string {
  const sanitized = baseTag.replace(/[:/]/g, '-')
  return `${sanitized}${OLLAMA_JARVIS_CTX_MARKER}:${numCtx}`
}

/** Daemon root for native /api/* calls — the provider baseUrl carries /v1. */
function daemonRoot(baseUrl: string): string {
  return baseUrl.replace(/\/+$/, '').replace(/\/v1$/, '')
}

// Memoized per (daemon, base tag, numCtx). Success memoizes forever (the
// manifest exists); failure clears the entry so a transiently-down daemon
// retries on the next request instead of pinning the degraded base tag.
const ensured = new Map<string, Promise<string>>()

/** Test hook — clears the ensure cache. */
export function _resetOllamaContextCacheForTests(): void {
  ensured.clear()
}

async function createDerivedModel(
  root: string,
  baseTag: string,
  numCtx: number,
  fetchImpl: typeof fetch,
): Promise<string> {
  const derived = derivedOllamaModelName(baseTag, numCtx)
  const res = await fetchImpl(`${root}/api/create`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model: derived,
      from: baseTag,
      parameters: { num_ctx: numCtx },
      stream: false,
    }),
  })
  if (!res.ok) {
    let body = ''
    try {
      body = await res.text()
    } catch {}
    throw new Error(`HTTP ${res.status}: ${body.slice(0, 200)}`)
  }
  console.log(
    `[jarvis-proxy] ollama: routing "${baseTag}" via "${derived}" (num_ctx=${numCtx}, JARVIS_OLLAMA_NUM_CTX). ` +
      `Note: on a ~6 GB GPU a context ≥12k spills to CPU and slows generation — lower JARVIS_OLLAMA_NUM_CTX, ` +
      `or set OLLAMA_KV_CACHE_TYPE=q8_0 + OLLAMA_FLASH_ATTENTION=1 on the daemon to keep more on-GPU.`,
  )
  // Best-effort hygiene: drop stale -jarvis-ctx variants of the SAME base
  // left behind by a previous JARVIS_OLLAMA_NUM_CTX value (manifest-only;
  // never touches the base model or other bases' variants).
  void cleanupStaleVariants(root, baseTag, derived, fetchImpl)
  return derived
}

async function cleanupStaleVariants(
  root: string,
  baseTag: string,
  keep: string,
  fetchImpl: typeof fetch,
): Promise<void> {
  try {
    const res = await fetchImpl(`${root}/api/tags`)
    if (!res.ok) return
    const data = (await res.json()) as {
      models?: Array<{ name?: string; model?: string }>
    }
    const prefix = `${baseTag.replace(/[:/]/g, '-')}${OLLAMA_JARVIS_CTX_MARKER}:`
    for (const m of data.models ?? []) {
      const tag = (m.model ?? m.name ?? '').trim()
      if (!tag || tag === keep || !tag.startsWith(prefix)) continue
      await fetchImpl(`${root}/api/delete`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: tag }),
      }).catch(() => {})
    }
  } catch {
    // Hygiene only — never let cleanup interfere with the request.
  }
}

/**
 * For an Ollama-routed request, return a Provider whose `model` is the
 * context-bumped derived name (creating it on the daemon if needed).
 * Non-ollama providers, already-derived tags, disabled knob, or a failed
 * create all return the provider unchanged — the request then runs exactly
 * as it would have before this module existed.
 */
export async function ensureOllamaNumCtx(
  provider: Provider,
  fetchImpl: typeof fetch = fetch,
  env: NodeJS.ProcessEnv = process.env,
): Promise<Provider> {
  if (provider.name !== 'ollama') return provider
  if (isJarvisCtxDerivedTag(provider.model)) return provider
  if (ollamaNumCtxDisabled(env)) return provider

  const numCtx = resolveOllamaNumCtx(env)
  const root = daemonRoot(provider.baseUrl)
  const key = `${root}|${provider.model}|${numCtx}`

  let pending = ensured.get(key)
  if (!pending) {
    pending = createDerivedModel(root, provider.model, numCtx, fetchImpl)
    ensured.set(key, pending)
    pending.catch(() => ensured.delete(key))
  }

  try {
    const derived = await pending
    return { ...provider, model: derived }
  } catch (e) {
    console.warn(
      `[jarvis-proxy] ollama: could not create num_ctx=${numCtx} variant of "${provider.model}" ` +
        `(${(e as Error).message}) — falling back to the base tag at the daemon-default context`,
    )
    return provider
  }
}
