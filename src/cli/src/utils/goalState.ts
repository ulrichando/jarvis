/**
 * /goal — session-scoped completion-condition state.
 *
 * Module-level singleton (one goal per process, like other session state).
 * Pure state container: no imports, no I/O — safe to import from both the
 * command handler and the stop-hook path without creating module cycles.
 *
 * The runaway cap lives here because stop-hook continuations do NOT bump
 * query.ts turnCount (the stop_hook_blocking transition preserves it), so
 * maxTurns never sees them — this cap is the only guard against an
 * evaluator that keeps answering NO forever.
 */

export type GoalClearReason = 'user' | 'met' | 'cap' | 'replaced' | 'error'

export type GoalState = {
  /** The user's completion condition, verbatim. */
  condition: string
  /** Date.now() when the goal was set. */
  setAt: number
  /** Automatic continuations fired so far for this goal. */
  continuations: number
  /** Most recent evaluator reason (null before the first evaluation). */
  lastReason: string | null
  /**
   * getTotalInputTokens()+getTotalOutputTokens() snapshot at set time,
   * so /goal status can show the token delta spent on the goal.
   */
  tokensAtSet: number
}

export const GOAL_DEFAULT_MAX_CONTINUATIONS = 25
export const GOAL_ABSOLUTE_MAX_CONTINUATIONS = 500

let activeGoal: GoalState | null = null
let lastClear: {
  reason: GoalClearReason
  condition: string
  at: number
} | null = null

/**
 * Hard runaway cap for automatic continuations.
 * JARVIS_GOAL_MAX_CONTINUATIONS env override; default 25; absolute ceiling
 * 500. 0 is legal (evaluate once, never continue). Invalid/negative values
 * fall back to the default — never to "unlimited".
 */
export function getGoalMaxContinuations(): number {
  const raw = process.env.JARVIS_GOAL_MAX_CONTINUATIONS
  if (raw === undefined || raw.trim() === '') {
    return GOAL_DEFAULT_MAX_CONTINUATIONS
  }
  const parsed = Number.parseInt(raw, 10)
  if (Number.isNaN(parsed) || parsed < 0) {
    return GOAL_DEFAULT_MAX_CONTINUATIONS
  }
  return Math.min(parsed, GOAL_ABSOLUTE_MAX_CONTINUATIONS)
}

export function setGoal(condition: string, tokensAtSet: number): GoalState {
  if (activeGoal) {
    lastClear = {
      reason: 'replaced',
      condition: activeGoal.condition,
      at: Date.now(),
    }
  }
  activeGoal = {
    condition,
    setAt: Date.now(),
    continuations: 0,
    lastReason: null,
    tokensAtSet,
  }
  return activeGoal
}

export function getGoal(): GoalState | null {
  return activeGoal
}

/** Clear the active goal (no-op when none). Returns the cleared goal. */
export function clearGoal(reason: GoalClearReason): GoalState | null {
  const cleared = activeGoal
  if (cleared) {
    lastClear = { reason, condition: cleared.condition, at: Date.now() }
  }
  activeGoal = null
  return cleared
}

/** Why/when the previous goal ended — for /goal status after a clear. */
export function getLastClear(): {
  reason: GoalClearReason
  condition: string
  at: number
} | null {
  return lastClear
}

/**
 * Record one automatic continuation. Returns false at the cap — the caller
 * must stop the loop (and clear the goal) instead of continuing. The
 * evaluator's reason is recorded either way so /goal status always shows
 * the latest verdict.
 */
export function recordContinuation(reason: string): boolean {
  if (!activeGoal) {
    return false
  }
  activeGoal.lastReason = reason
  if (activeGoal.continuations >= getGoalMaxContinuations()) {
    return false
  }
  activeGoal.continuations += 1
  return true
}
