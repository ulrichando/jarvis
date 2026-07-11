/**
 * Lean request shaping for local Ollama models (P0-b of the
 * local-model-usable fix, 2026-07-11).
 *
 * A full jarvis request is ~40k tokens (multi-section system prompt + 25+
 * tool schemas + MCP tools) — far beyond what a 4B on-device model can use
 * productively, and bigger than any GPU-resident num_ctx on a 6 GB card.
 * When the request's model resolves to provider 'ollama' in the jarvis
 * registry, we:
 *   1. gate the exposed toolset down to the lean core below
 *      (services/api/claude.ts, request time — reacts to /model switches
 *      and covers subagents that run on a local model), and
 *   2. serve a compact system prompt (constants/prompts.ts::getSystemPrompt).
 *
 * Cloud models never hit either path — requests for them are byte-identical.
 * Kill-switch: JARVIS_OLLAMA_LEAN=0 restores the full prompt + toolset for
 * local models (useful on a box with a big GPU).
 */
import {
  getJarvisModel,
  isJarvisModelRegistryEnabled,
} from './model/jarvisModelRegistry.js'

/**
 * The lean core toolset — file read/write/edit, search, and shell. Enough
 * for real local work; small enough (≈4–6k schema tokens vs ≈25k+ for the
 * full set) that a small model can attend to all of it. MCP tools and the
 * long tail (Task/WebFetch/Skill/Todo/…) are deliberately excluded: small
 * models mis-call them far more than they use them.
 */
export const LOCAL_LEAN_TOOL_NAMES: ReadonlySet<string> = new Set([
  'Bash',
  'Read',
  'Edit',
  'Write',
  'Glob',
  'Grep',
])

function leanDisabled(env: NodeJS.ProcessEnv = process.env): boolean {
  const v = (env.JARVIS_OLLAMA_LEAN ?? '').trim().toLowerCase()
  return v === '0' || v === 'false' || v === 'no' || v === 'off'
}

/**
 * True when `model` is a local Ollama model that should get the lean
 * request shape. False for cloud models, unknown model strings, a disabled
 * registry, or when the JARVIS_OLLAMA_LEAN=0 kill-switch is set.
 */
export function isLeanLocalModel(
  model: string | null | undefined,
  env: NodeJS.ProcessEnv = process.env,
): boolean {
  if (!model) return false
  if (!isJarvisModelRegistryEnabled()) return false
  if (leanDisabled(env)) return false
  return getJarvisModel(model)?.provider === 'ollama'
}

/**
 * Request-time tool gate: for lean local models, keep only the lean core.
 * Anything else (cloud model, kill-switch, unknown model) passes through
 * untouched. Defensive: if filtering would produce an EMPTY toolset (e.g. a
 * caller already narrowed tools to a disjoint set), the input is returned
 * unchanged — an odd toolset beats a tool-less agent.
 */
export function filterToolsForLeanLocalModel<T extends { name: string }>(
  tools: readonly T[],
  model: string | null | undefined,
  env: NodeJS.ProcessEnv = process.env,
): readonly T[] {
  if (tools.length === 0 || !isLeanLocalModel(model, env)) return tools
  const lean = tools.filter(t => LOCAL_LEAN_TOOL_NAMES.has(t.name))
  return lean.length > 0 ? lean : tools
}
