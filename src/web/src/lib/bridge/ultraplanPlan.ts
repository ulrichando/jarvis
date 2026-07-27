import { type SessionEventRow } from "./store";

// Pure helpers for the /ultraplan plan-decision flow, extracted from the plan
// route so they're unit-testable without a DB. The markers MUST match what the
// CLI poller extracts in src/cli/src/utils/ultraplan/ccrSession.ts
// (extractApprovedPlan / extractTeleportPlan).

export type PlanDecision = "approve" | "reject" | "local";

/**
 * Build the `tool_result` { is_error, content } the worker delivers to resolve
 * a pending ExitPlanMode call:
 *   approve → is_error:false, "## Approved Plan:\n<plan>" (+ "(edited by user)")
 *   local   → is_error:true,  "__ULTRAPLAN_TELEPORT_LOCAL__\n<plan>" (run locally)
 *   reject  → is_error:true,  no sentinel (the poller iterates); optional user
 *             feedback is appended so the agent revises WITH the user's notes.
 *             With empty/absent feedback the string stays EXACTLY
 *             "Plan rejected by user." — and it must never contain the approve
 *             marker or teleport sentinel, since the poller classifies reject
 *             by their absence.
 */
export function buildPlanDecision(
  decision: PlanDecision,
  plan: string,
  edited: boolean,
  feedback?: string,
): { isError: boolean; content: string } {
  if (decision === "approve") {
    const marker = edited
      ? "## Approved Plan (edited by user):"
      : "## Approved Plan:";
    return { isError: false, content: `${marker}\n${plan}` };
  }
  if (decision === "local") {
    return { isError: true, content: `__ULTRAPLAN_TELEPORT_LOCAL__\n${plan}` };
  }
  // Feedback is the first free-text channel into a reject tool_result. Neutralize
  // the poller's classification markers if the user typed them, so a rejection can
  // never mis-classify as teleport (extractTeleportPlan scans the whole content
  // for the sentinel) or approve.
  const note = feedback
    ?.trim()
    .split("__ULTRAPLAN_TELEPORT_LOCAL__")
    .join("[marker removed]")
    .split("## Approved Plan:")
    .join("## Approved-Plan:");
  return {
    isError: true,
    content: note
      ? `Plan rejected by user.\n\nFeedback to address:\n${note}`
      : "Plan rejected by user.",
  };
}

/**
 * Scan a session's events for ExitPlanMode tool calls and their results.
 * Returns the tool_use id of the NEWEST ExitPlanMode call that has no
 * tool_result yet (the plan awaiting a decision), or null. Only reads
 * `payload_json`, so synthetic rows can drive it in tests.
 */
export function findPendingExitPlanToolUseId(
  events: Array<Pick<SessionEventRow, "payload_json">>,
): string | null {
  const calls: string[] = [];
  const resolved = new Set<string>();
  for (const e of events) {
    let payload: Record<string, unknown>;
    try {
      payload = JSON.parse(e.payload_json) as Record<string, unknown>;
    } catch {
      continue;
    }
    const content = (payload.message as { content?: unknown } | undefined)
      ?.content;
    if (!Array.isArray(content)) continue;
    for (const block of content as Array<Record<string, unknown>>) {
      if (
        block?.type === "tool_use" &&
        typeof block?.name === "string" &&
        /exit_?plan_?mode/i.test(block.name) &&
        typeof block?.id === "string"
      ) {
        calls.push(block.id);
      }
      if (
        block?.type === "tool_result" &&
        typeof block?.tool_use_id === "string"
      ) {
        resolved.add(block.tool_use_id);
      }
    }
  }
  for (let i = calls.length - 1; i >= 0; i--) {
    if (!resolved.has(calls[i]!)) return calls[i]!;
  }
  return null;
}

/**
 * Container sessions gate ExitPlanMode behind a `can_use_tool` permission that
 * the worker/events route records as an `ultraplan_permission` event (and
 * deliberately does NOT auto-approve — the plan must PAUSE for a human
 * decision). Returns the newest such permission for the given ExitPlanMode
 * tool_use that hasn't been resolved yet (no matching
 * `ultraplan_permission_resolved`): its request_id and the ORIGINAL tool input
 * (so the approve path replays exactly what the auto-approve path used to send
 * — the plan is read from disk, not input.plan, so the original input must be
 * preserved or the tool hits its empty-plan branch and drops the "## Approved
 * Plan:" marker the poller scans for). Browser-driven local sessions have no
 * such event → null → the /plan route falls back to injecting a tool_result.
 */
export function findUnresolvedPlanPermission(
  events: Array<Pick<SessionEventRow, "type" | "payload_json">>,
  toolUseId: string,
): { requestId: string; input: Record<string, unknown> } | null {
  const resolved = new Set<string>();
  let match: { requestId: string; input: Record<string, unknown> } | null =
    null;
  for (const e of events) {
    let p: {
      request_id?: unknown;
      tool_use_id?: unknown;
      input?: unknown;
    };
    try {
      p = JSON.parse(e.payload_json) as typeof p;
    } catch {
      continue;
    }
    if (e.type === "ultraplan_permission_resolved") {
      if (typeof p.request_id === "string") resolved.add(p.request_id);
    } else if (
      e.type === "ultraplan_permission" &&
      p.tool_use_id === toolUseId &&
      typeof p.request_id === "string"
    ) {
      match = {
        requestId: p.request_id,
        input:
          p.input && typeof p.input === "object"
            ? (p.input as Record<string, unknown>)
            : {},
      }; // newest wins
    }
  }
  return match && !resolved.has(match.requestId) ? match : null;
}
