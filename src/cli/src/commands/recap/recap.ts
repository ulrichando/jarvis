import { generateAwaySummary } from '../../services/awaySummary.js'
import type { LocalCommandCall } from '../../types/command.js'
import { createAwaySummaryMessage } from '../../utils/messages.js'

const EMPTY_RECAP_MESSAGE =
  'Nothing to recap yet — have a conversation first, then try /recap again.'

/**
 * /recap — on-demand session recap, rendered as the same dim "※" card the
 * automatic away-summary appends (useAwaySummary.ts). Reuses
 * generateAwaySummary end-to-end; only the prompt lead-in differs ('manual').
 */
export const call: LocalCommandCall = async (_args, context) => {
  const text = await generateAwaySummary(
    context.messages,
    context.abortController.signal,
    'manual',
  )

  // ESC during generation → behave like any interrupted command: no output.
  if (context.abortController.signal.aborted) {
    return { type: 'skip' }
  }

  // Empty session, API error, or empty model output all collapse to null in
  // generateAwaySummary — show a friendly notice (standard local-command
  // stdout, not the ※ card) instead of silently doing nothing.
  if (text === null || text.trim().length === 0) {
    return { type: 'text', value: EMPTY_RECAP_MESSAGE }
  }

  // Headless (-p / SDK): context.setMessages is a no-op stub there
  // (QueryEngine.ts) and Ink never renders — return the text so it flows out
  // via SlashCommandResult.resultText and prints as the -p result.
  if (context.options.isNonInteractiveSession) {
    return { type: 'text', value: text }
  }

  // Interactive: append the exact message shape the automatic feature appends
  // (dim "※" card via SystemTextMessage), then skip — no command echo, no
  // stdout block, identical to the auto card.
  context.setMessages(prev => [...prev, createAwaySummaryMessage(text)])
  return { type: 'skip' }
}
