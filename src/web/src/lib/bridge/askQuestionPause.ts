// AskUserQuestion pause for web /code container sessions.
//
// Container sessions run bypassPermissions with no TTY, so the worker/events
// route auto-approves every can_use_tool — except the ones that genuinely need
// a human. ExitPlanMode is one (handled via ultraplanPlan). AskUserQuestion is
// the other: auto-approving it replays the input with NO `answers` field, so
// the tool tells the model "User has answered your questions: ." and the agent
// stalls. Instead we PAUSE it as a requires_action pending_action so the
// browser's QuestionsCard (code-session.tsx) renders and the /messages
// permission answer delivers {...input, answers} back.
//
// The shapes below MUST match what code-session.tsx reads:
//   pendingAction(): worker_status === 'requires_action' + a pending_action
//     carrying request_id.
//   askQuestions(): pending_action.tool_name === 'AskUserQuestion' (exact) and
//     pending_action.input.questions is a non-empty array.
//
// KNOWN LIMITATION (deferred): like ExitPlanMode, this pauses indefinitely with
// no reaper. For an interactive /code or a Dispatch (push-notified) session a
// human returns to answer. For a FULLY autonomous run (routines / gh-app /
// autofix — dispatch=0 and no browser), a model that asks a question now blocks
// forever and keeps the container lease alive. `dispatch` does not distinguish
// those from interactive /code (both are dispatch=0), so there is no clean
// signal here to auto-deny; that needs a dedicated "no human" session flag.

/** Gate matching the tool name the CLI emits (PascalCase or snake_case). */
export function isAskUserQuestionTool(toolName: string | undefined): boolean {
  return /^ask_?user_?question$/i.test(toolName ?? '')
}

/**
 * The mergeWorkerState update that pauses an AskUserQuestion for a human
 * answer. `input` is the tool's original input ({ questions: [...] }).
 */
export function buildAskQuestionPauseState(
  requestId: string,
  input: unknown,
): Record<string, unknown> {
  return {
    worker_status: 'requires_action',
    external_metadata: {
      pending_action: {
        request_id: requestId,
        // Exact PascalCase — code-session.tsx's askQuestions() compares against
        // the literal "AskUserQuestion".
        tool_name: 'AskUserQuestion',
        action_description: 'Answer questions',
        input: input && typeof input === 'object' ? input : {},
      },
    },
  }
}

/**
 * The mergeWorkerState update that clears the pause once the answer is sent, so
 * the card disappears and the spinner resumes. pending_action:null drops it
 * from pendingAction() regardless of the reported status.
 */
export const ASK_QUESTION_PAUSE_CLEAR: Record<string, unknown> = {
  worker_status: 'running',
  external_metadata: { pending_action: null },
}

/**
 * Drops the pending_action WITHOUT forcing a status, for abandonment paths
 * (Stop/interrupt) where the worker is heading to idle, not running. Forcing
 * 'running' there would strand an idle worker behind a permanent spinner.
 */
export const ASK_QUESTION_PENDING_ONLY_CLEAR: Record<string, unknown> = {
  external_metadata: { pending_action: null },
}

/**
 * The request_id of the question currently paused for this session, or
 * undefined. Used to make the answer-clear CONDITIONAL: only reset worker
 * state when the incoming answer resolves the live question — a late / double /
 * phantom answer whose id no longer matches must not flip an idle worker to
 * 'running' (stuck spinner) or clear a newer pause.
 */
export function pausedRequestId(
  workerStateJson: string | null | undefined,
): string | undefined {
  if (!workerStateJson) return undefined
  try {
    const ws = JSON.parse(workerStateJson) as {
      external_metadata?: { pending_action?: { request_id?: unknown } | null }
    }
    const id = ws.external_metadata?.pending_action?.request_id
    return typeof id === 'string' ? id : undefined
  } catch {
    return undefined
  }
}
