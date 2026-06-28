/**
 * Periodic task summary for `jarvis ps` (BG_SESSIONS feature).
 *
 * Writes a short "what is this session working on" string into the session's
 * PID registry file so `jarvis ps` can show it. Derives the summary from the
 * most recent user message rather than forking an LLM call — keeps it free of
 * latency/cost on every turn. ponytail: text-derived summary; swap for an LLM
 * fork only if ps summaries prove too coarse.
 */
import type { Message } from '../types/message.js'
import type { SystemPrompt } from './systemPromptType.js'
import type { ToolUseContext } from '../Tool.js'
import { updateSessionName } from './concurrentSessions.js'
import { logForDebugging } from './debug.js'
import { errorMessage } from './errors.js'

const SUMMARY_THROTTLE_MS = 30_000
const MAX_SUMMARY_LEN = 72

let lastSummaryAt = 0

/** True if enough time has elapsed since the last summary write. */
export function shouldGenerateTaskSummary(): boolean {
  return Date.now() - lastSummaryAt >= SUMMARY_THROTTLE_MS
}

/** Extract plain text from a message's content (string or block array). */
function messageText(m: Message): string {
  const content = m.message.content
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return ''
  return content
    .filter((b): b is { type: 'text'; text: string } => b.type === 'text')
    .map(b => b.text)
    .join(' ')
}

/**
 * Best-effort: derive a one-line task summary from the latest user message and
 * persist it to the session registry. Fire-and-forget — never throws.
 */
export function maybeGenerateTaskSummary(opts: {
  systemPrompt: SystemPrompt
  userContext: { [k: string]: string }
  systemContext: { [k: string]: string }
  toolUseContext: ToolUseContext
  forkContextMessages: Message[]
}): void {
  lastSummaryAt = Date.now()

  // Walk backwards for the most recent non-empty user message.
  const messages = opts.forkContextMessages
  let summary = ''
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]!
    if (m.type !== 'user') continue
    const text = messageText(m).trim()
    // Skip system-reminder / tool-result style synthetic user turns.
    if (!text || text.startsWith('<')) continue
    summary = text.replace(/\s+/g, ' ').slice(0, MAX_SUMMARY_LEN)
    break
  }

  if (!summary) return

  void updateSessionName(summary).catch(e => {
    logForDebugging(`[taskSummary] write failed: ${errorMessage(e)}`)
  })
}
