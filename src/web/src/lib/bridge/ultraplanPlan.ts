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
 *   reject  → is_error:true,  no sentinel (the poller iterates)
 */
export function buildPlanDecision(
  decision: PlanDecision,
  plan: string,
  edited: boolean,
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
  return { isError: true, content: "Plan rejected by user." };
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
