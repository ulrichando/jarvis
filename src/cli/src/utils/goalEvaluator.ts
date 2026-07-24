import { APIUserAbortError } from '@anthropic-ai/sdk/index.js'
import type { QuerySource } from '../constants/querySource.js'
import { queryModelWithoutStreaming } from '../services/api/claude.js'
import { getEmptyToolPermissionContext } from '../Tool.js'
import type { Message } from '../types/message.js'
import { logForDebugging } from './debug.js'
import { createUserMessage, getAssistantMessageText } from './messages.js'
import { getSmallFastModel } from './model/model.js'
import { asSystemPrompt } from './systemPromptType.js'

/**
 * /goal completion evaluator.
 *
 * Deliberately mirrors the PROVEN awaySummary.ts shape (plain-text
 * queryModelWithoutStreaming + getSmallFastModel + thinking disabled +
 * skipCacheWrite) rather than structured/json_schema output — structured
 * output through the multi-provider proxy is unverified, and a broken judge
 * must NEVER cause an infinite loop.
 *
 * Fail-open contract: any error, API failure, empty reply, or ambiguous
 * reply returns { met: true } so the auto-continue loop STOPS. Stopping is
 * the safe failure; looping is not. Abort is the one exception the caller
 * must handle: it re-checks signal.aborted after this returns and stops
 * without clearing the goal.
 */

// Judge only needs recent context — bound the window to avoid prompt-too-long
// on large sessions (same rationale as awaySummary's RECENT_MESSAGE_WINDOW).
const GOAL_EVAL_MESSAGE_WINDOW = 40

// Keep the fed-back reason bounded so continuation messages can't bloat.
const MAX_REASON_LENGTH = 400

export type GoalVerdict = { met: boolean; reason: string; failOpen?: boolean }

function buildJudgePrompt(condition: string): string {
  return `You are a strict completion judge for an automated coding session. The user set this completion condition:

<goal_condition>
${condition}
</goal_condition>

Decide whether the condition is FULLY met RIGHT NOW, based only on concrete evidence visible in the conversation above — tool outputs, command results, test output, file contents. Do NOT trust the assistant's claims of success unless tool output confirms them. Partial progress counts as NO.

If the condition specifies a COUNT, REPETITION, or set of steps ("at least N times", "in 3 separate turns", "for each X"), count the CONFIRMED occurrences and answer NO unless that exact number is definitively reached. Never round up from partial progress, and if you are unsure whether the count is reached, answer NO.

Reply in EXACTLY this format:
- First line: the single word YES (condition fully met) or NO (not yet met)
- Following lines: 1-2 short sentences of reasoning. If NO, state concretely what is still missing.`
}

function truncateReason(reason: string): string {
  const collapsed = reason.replace(/\s+/g, ' ').trim()
  return collapsed.length > MAX_REASON_LENGTH
    ? `${collapsed.slice(0, MAX_REASON_LENGTH)}…`
    : collapsed
}

/**
 * Parse the judge reply: first non-empty line must start with YES or NO
 * (markdown decoration like "**NO** — ..." tolerated). Returns null when the
 * reply doesn't clearly start with either — caller fails open.
 */
export function parseGoalVerdict(text: string): GoalVerdict | null {
  const lines = text.split('\n')
  const firstIdx = lines.findIndex(l => l.trim().length > 0)
  if (firstIdx === -1) {
    return null
  }
  // Strip leading non-letters (markdown bullets/emphasis/quotes) before
  // matching the verdict token.
  const first = lines[firstIdx]!.trim().replace(/^[^A-Za-z]+/, '')
  const match = /^(yes|no)\b[\s*:.,;—-]*(.*)$/i.exec(first)
  if (!match) {
    return null
  }
  const met = match[1]!.toLowerCase() === 'yes'
  const restOfFirstLine = (match[2] ?? '').replace(/\*+/g, '').trim()
  const followingLines = lines
    .slice(firstIdx + 1)
    .join(' ')
    .trim()
  const reason =
    [restOfFirstLine, followingLines].filter(Boolean).join(' ').trim() ||
    (met ? 'Condition met.' : 'Condition not yet met.')
  return { met, reason: truncateReason(reason) }
}

/**
 * Judge whether the goal condition is met, given the conversation so far.
 * Never throws. See fail-open contract above.
 */
export async function evaluateGoal(
  messages: readonly Message[],
  condition: string,
  signal: AbortSignal,
): Promise<GoalVerdict> {
  try {
    const recent = messages.slice(-GOAL_EVAL_MESSAGE_WINDOW)
    recent.push(createUserMessage({ content: buildJudgePrompt(condition) }))
    const response = await queryModelWithoutStreaming({
      messages: recent,
      systemPrompt: asSystemPrompt([]),
      thinkingConfig: { type: 'disabled' },
      tools: [],
      signal,
      options: {
        getToolPermissionContext: async () => getEmptyToolPermissionContext(),
        model: getSmallFastModel(),
        toolChoice: undefined,
        isNonInteractiveSession: false,
        hasAppendSystemPrompt: false,
        agents: [],
        querySource: 'goal_evaluator' as QuerySource,
        mcpTools: [],
        skipCacheWrite: true,
      },
    })

    if (response.isApiErrorMessage) {
      const errText = getAssistantMessageText(response) ?? 'unknown API error'
      logForDebugging(`[goalEvaluator] API error: ${errText}`)
      return {
        met: true,
        failOpen: true,
        reason: `Evaluator API error (${truncateReason(errText)}) — stopping auto-continue as a precaution.`,
      }
    }

    const text = getAssistantMessageText(response)
    if (!text || text.trim().length === 0) {
      logForDebugging('[goalEvaluator] empty evaluator reply')
      return {
        met: true,
        failOpen: true,
        reason:
          'Evaluator returned an empty reply — stopping auto-continue as a precaution.',
      }
    }

    const verdict = parseGoalVerdict(text)
    if (!verdict) {
      logForDebugging(
        `[goalEvaluator] ambiguous evaluator reply: ${text.slice(0, 200)}`,
      )
      return {
        met: true,
        failOpen: true,
        reason: `Evaluator reply did not start with YES/NO ("${truncateReason(text).slice(0, 120)}") — stopping auto-continue as a precaution.`,
      }
    }
    return verdict
  } catch (err) {
    if (err instanceof APIUserAbortError || signal.aborted) {
      // Caller re-checks signal.aborted and stops without clearing the goal;
      // this value is only used if the signal somehow reads un-aborted.
      return { met: true, reason: 'Evaluation aborted — auto-continue stopped.' }
    }
    logForDebugging(`[goalEvaluator] evaluation failed: ${err}`)
    return {
      met: true,
      failOpen: true,
      reason: `Evaluator failed (${truncateReason(String(err))}) — stopping auto-continue as a precaution.`,
    }
  }
}
