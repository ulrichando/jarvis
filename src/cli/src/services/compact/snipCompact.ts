import type { Message } from '../../types/message.js'
import { tokenCountWithEstimation } from '../../utils/tokens.js'
import {
  resolveSnipRange,
  buildSnipBoundary,
  estimateTokensFreed,
} from './snipRange.js'
import { projectSnippedView, isSnipBoundaryMessage } from './snipProjection.js'

export const SNIP_NUDGE_TEXT =
  'Context is growing. If earlier exploration is concluded or superseded, consider using the Snip tool with the [id:] anchors to remove those ranges from context. Never snip anything still needed for the current task.'

const NUDGE_INTERVAL_TOKENS = 10_000

// Env kill-switch. The compile-time feature() gate is the real switch; this
// lets an operator disable the runtime without a rebuild.
export function isSnipRuntimeEnabled(): boolean {
  return process.env.JARVIS_HISTORY_SNIP !== '0'
}

// Pending Snip tool-uses. SnipTool.call() enqueues; snipCompactIfNeeded drains
// at the query boundary (tools must not mutate the store mid-turn).
type PendingSnip = { startId: string; endId: string }
let pendingSnips: PendingSnip[] = []
let lastNudgeTokens = 0

export function _queueSnip(startId: string, endId: string): void {
  pendingSnips.push({ startId, endId })
}
export function _resetSnipQueueForTest(): void {
  pendingSnips = []
  lastNudgeTokens = 0
}

// Nudge once every ~10k tokens of growth; reset on nudge / snip.
export function shouldNudgeForSnips(messages: Message[]): boolean {
  if (!isSnipRuntimeEnabled()) return false
  const now = tokenCountWithEstimation(messages as Message[])
  if (now - lastNudgeTokens >= NUDGE_INTERVAL_TOKENS) {
    lastNudgeTokens = now
    return true
  }
  return false
}

export type SnipResult = {
  messages: Message[]
  tokensFreed: number
  executed: boolean
  boundaryMessage?: Message
}

export function snipCompactIfNeeded(
  messages: Message[],
  _options?: { force?: boolean },
): SnipResult {
  if (!isSnipRuntimeEnabled() || pendingSnips.length === 0) {
    return { messages, tokensFreed: 0, executed: false }
  }

  const queued = pendingSnips
  pendingSnips = []

  let working = messages
  let totalFreed = 0
  let lastBoundary: Message | undefined
  let anyExecuted = false

  for (const snip of queued) {
    const range = resolveSnipRange(working, snip.startId, snip.endId)
    if ('error' in range) continue // invalid range → skip silently (best-effort)
    const freed = estimateTokensFreed(working, range.removedUuids)
    const boundary = buildSnipBoundary(range.removedUuids, freed)
    working = [
      ...working.slice(0, range.endIndex + 1),
      boundary,
      ...working.slice(range.endIndex + 1),
    ]
    totalFreed += freed
    lastBoundary = boundary
    anyExecuted = true
    lastNudgeTokens = 0
  }

  if (!anyExecuted) {
    return { messages, tokensFreed: 0, executed: false }
  }

  return {
    messages: working,
    tokensFreed: totalFreed,
    executed: true,
    boundaryMessage: lastBoundary,
  }
}

// Re-export so call sites importing from snipCompact keep working.
export { projectSnippedView, isSnipBoundaryMessage }
