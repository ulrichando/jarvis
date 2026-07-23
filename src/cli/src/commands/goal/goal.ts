import {
  getTotalInputTokens,
  getTotalOutputTokens,
} from '../../cost-tracker.js'
import type { LocalJSXCommandCall } from '../../types/command.js'
import { checkHasTrustDialogAccepted } from '../../utils/config.js'
import { formatDuration } from '../../utils/format.js'
import {
  clearGoal,
  getGoal,
  getGoalMaxContinuations,
  getLastClear,
  setGoal,
} from '../../utils/goalState.js'

/**
 * /goal — session-scoped completion condition with automatic continuation.
 *
 * /goal <condition>  set the goal and kick off a turn; after each turn the
 *                    stop-hook tail (query/stopHooks.ts) asks a small fast
 *                    model whether the condition is met and auto-continues
 *                    until YES, /goal clear, Ctrl+C, or the continuation cap
 *                    (JARVIS_GOAL_MAX_CONTINUATIONS, default 25, max 500).
 * /goal              status (condition, elapsed, continuations, last verdict,
 *                    token delta since set).
 * /goal clear        stop (aliases: stop/off/reset/none/cancel).
 *
 * local-jsx command that never renders JSX: call() always invokes onDone()
 * and returns null, which works in the interactive REPL AND headless -p/SDK
 * (processSlashCommand.tsx resolves on onDone; non-interactive discards the
 * null JSX). The kick-off uses onDone(..., { shouldQuery: true,
 * metaMessages }) because that is the one chaining mechanism honored on both
 * paths — QueryEngine.submitMessage drops nextInput/submitNextInput.
 */

const CLEAR_ALIASES = new Set([
  'clear',
  'stop',
  'off',
  'reset',
  'none',
  'cancel',
])

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max)}…` : text
}

export const call: LocalJSXCommandCall = async (onDone, context, args) => {
  const arg = (args ?? '').trim()

  // Trust gate — /goal drives model turns automatically, so require the
  // workspace trust dialog in interactive mode (same rule as hook execution:
  // utils/hooks.ts shouldSkipHookDueToTrust). In non-interactive (-p / SDK)
  // trust is implicit, also matching hook semantics.
  if (
    !context.options.isNonInteractiveSession &&
    !checkHasTrustDialogAccepted()
  ) {
    onDone(
      '/goal is unavailable until workspace trust is accepted. Accept the trust dialog for this folder, then try again.',
    )
    return null
  }

  // ── /goal (no args) → status ──────────────────────────────────────────
  if (arg === '') {
    const goal = getGoal()
    if (!goal) {
      const last = getLastClear()
      const lastNote = last
        ? ` Previous goal ${
            last.reason === 'met'
              ? 'was met'
              : last.reason === 'cap'
                ? 'hit the continuation cap'
                : last.reason === 'error'
                  ? 'stopped on an evaluator problem'
                  : 'was cleared'
          }: "${truncate(last.condition, 80)}".`
        : ''
      onDone(`No active goal.${lastNote} Set one with /goal <condition>.`)
      return null
    }
    const cap = getGoalMaxContinuations()
    const tokensNow = getTotalInputTokens() + getTotalOutputTokens()
    onDone(
      [
        `Active goal: "${goal.condition}"`,
        `Set ${formatDuration(Date.now() - goal.setAt)} ago · continuations: ${goal.continuations}/${cap} · tokens since set: ${(tokensNow - goal.tokensAtSet).toLocaleString()}`,
        `Last evaluator verdict: ${goal.lastReason ?? '(not evaluated yet)'}`,
        `/goal clear (or Esc/Ctrl+C mid-loop) stops it.`,
      ].join('\n'),
    )
    return null
  }

  // ── /goal clear (and aliases) ─────────────────────────────────────────
  if (CLEAR_ALIASES.has(arg.toLowerCase())) {
    const cleared = clearGoal('user')
    onDone(
      cleared
        ? `Goal cleared: "${truncate(cleared.condition, 120)}" — auto-continue stopped.`
        : 'No active goal to clear.',
    )
    return null
  }

  // ── /goal <condition> → set + kick off a turn ─────────────────────────
  const condition = arg
  const previous = getGoal()
  const cap = getGoalMaxContinuations()
  setGoal(condition, getTotalInputTokens() + getTotalOutputTokens())
  const replacedNote = previous
    ? ` (replaced previous goal: "${truncate(previous.condition, 80)}")`
    : ''
  onDone(
    `Goal set${replacedNote}: "${truncate(condition, 200)}" — auto-continuing after each turn until the evaluator judges it met (cap: ${cap} continuations). /goal = status; /goal clear stops it (or Esc/Ctrl+C to interrupt mid-loop).`,
    {
      shouldQuery: true,
      metaMessages: [
        `[GOAL MODE] The user set a completion condition for this session: "${condition}". Start working toward it now. After each of your turns an automatic evaluator inspects the conversation and continues you until the condition is met (or a safety cap of ${cap} continuations is reached). The evaluator only trusts evidence visible in tool outputs — verify your work with tools rather than claiming success.`,
      ],
    },
  )
  return null
}
