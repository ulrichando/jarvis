// Persistent cache for DeepSeek thinking-mode reasoning_content, keyed by
// tool_use_id. DeepSeek requires the prior assistant turn's reasoning_content
// to be echoed back on follow-up turns. Anthropic's protocol has no field
// for this, so the proxy caches it server-side keyed by tool_use_id (which
// round-trips faithfully through Claude Code) and re-attaches it to the
// outgoing OpenAI request.
//
// Persisted to disk so cache survives proxy restarts — without persistence,
// any conversation in flight at restart would 400 on the next turn because
// the prior assistant tool_use_ids would be cache-missed.

import { readFileSync, writeFileSync, mkdirSync, renameSync } from 'node:fs'
import { dirname } from 'node:path'
import { homedir } from 'node:os'

const TTL_MS = 24 * 60 * 60 * 1000
const MAX_ENTRIES = 5000
// Per-entry cap: MAX_ENTRIES bounds the COUNT but not the SIZE, so one runaway
// reasoning_content could pin megabytes × 5000. Real DeepSeek reasoning is well
// under this; oversized entries are skipped and fall back to the placeholder
// (the same graceful degradation a restart cache-miss already uses).
const MAX_REASONING_BYTES = 1024 * 1024
const CACHE_PATH = `${homedir()}/.jarvis/reasoning-cache.json`

export const REASONING_PLACEHOLDER =
  '[prior reasoning context not preserved across proxy restart]'

type Entry = { reasoning: string; expiresAt: number }

const cache = new Map<string, Entry>()

let saveScheduled = false
function scheduleSave(): void {
  if (saveScheduled) return
  saveScheduled = true
  setTimeout(() => {
    saveScheduled = false
    flushToDisk()
  }, 250)
}

function flushToDisk(): void {
  try {
    mkdirSync(dirname(CACHE_PATH), { recursive: true })
    const obj: Record<string, Entry> = {}
    for (const [k, v] of cache) obj[k] = v
    // Atomic write: a crash mid-writeFileSync would truncate the cache file
    // in place (loadFromDisk then discards it → every in-flight conversation
    // loses its reasoning context). Write to a temp file then rename (atomic
    // on the same filesystem) so the live file is never partially written.
    const tmp = `${CACHE_PATH}.tmp-${process.pid}`
    writeFileSync(tmp, JSON.stringify(obj))
    renameSync(tmp, CACHE_PATH)
  } catch (e) {
    console.error('[reasoning-cache] flush failed:', (e as Error).message)
  }
}

function loadFromDisk(): void {
  try {
    const raw = readFileSync(CACHE_PATH, 'utf-8')
    const obj = JSON.parse(raw) as Record<string, Entry>
    const now = Date.now()
    for (const [k, v] of Object.entries(obj)) {
      if (v.expiresAt > now) cache.set(k, v)
    }
  } catch {
    // First run, or file missing/corrupt — start fresh.
  }
}

loadFromDisk()

export function setReasoning(toolUseId: string, reasoning: string): void {
  if (!toolUseId || !reasoning) return
  // Skip pathologically-large reasoning rather than pin it in memory + on disk
  // for up to 24h × 5000 entries. The follow-up turn cache-misses → placeholder.
  if (reasoning.length > MAX_REASONING_BYTES) return
  if (cache.size >= MAX_ENTRIES) {
    const oldest = cache.keys().next().value
    if (oldest !== undefined) cache.delete(oldest)
  }
  cache.set(toolUseId, { reasoning, expiresAt: Date.now() + TTL_MS })
  scheduleSave()
}

export function getReasoning(toolUseId: string): string | null {
  if (!toolUseId) return null
  const entry = cache.get(toolUseId)
  if (!entry) return null
  if (entry.expiresAt < Date.now()) {
    cache.delete(toolUseId)
    scheduleSave()
    return null
  }
  return entry.reasoning
}
