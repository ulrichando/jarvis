import type { UIMessage } from 'ai'

/**
 * The message history sent to the chat API for one turn: normally the current
 * render's `messages` + the new user message.
 *
 * But a multi-stage build auto-fires the next stage by re-entering submit()
 * from a queueMicrotask — with a STALE `messages` closure captured before the
 * just-finished stage's reply landed. Sending stage N+1 without stage N in
 * history is why "long builds go off the rails" (#161 finding #5). When the
 * caller holds the finalized history (prior turns + this stage's reply), it
 * passes it as `continuationHistory`, and it takes precedence over the stale
 * closure value.
 */
export function buildTurnHistory(
  priorMessages: UIMessage[],
  userMessage: UIMessage,
  continuationHistory?: UIMessage[],
): UIMessage[] {
  return [...(continuationHistory ?? priorMessages), userMessage]
}
