import { NextResponse } from "next/server";
import { randomBytes } from "node:crypto";
import { getStore } from "@/lib/bridge/db";
import {
  appendInbound,
  appendSessionEvent,
  listSessionEvents,
} from "@/lib/bridge/store";
import { emitInbound } from "@/lib/bridge/events";
import { authorizeSession } from "@/lib/bridge/authz";
import { bridgeError } from "@/lib/bridge/errors";
import {
  buildPlanDecision,
  findPendingExitPlanToolUseId,
  findUnresolvedPlanPermission,
} from "@/lib/bridge/ultraplanPlan";

// GET /api/bridge/v1/sessions/{id}/plan — the latest plan the agent proposed
// in plan mode (an ExitPlanMode tool call in an assistant turn). Read-only;
// returns an empty plan when there is none yet, so the panel can poll.
export async function GET(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params;
  const denied = await authorizeSession(req, sessionId);
  if (denied) return denied;
  try {
    const events = listSessionEvents(getStore(), sessionId, 0);
    let plan = "";
    for (const e of events) {
      let payload: Record<string, unknown>;
      try {
        payload = JSON.parse(e.payload_json) as Record<string, unknown>;
      } catch {
        continue;
      }
      const content = (payload.message as { content?: unknown } | undefined)?.content;
      if (!Array.isArray(content)) continue;
      for (const block of content as Array<Record<string, unknown>>) {
        if (
          block?.type === "tool_use" &&
          typeof block?.name === "string" &&
          /exit_?plan_?mode/i.test(block.name)
        ) {
          const p = (block.input as { plan?: unknown } | undefined)?.plan;
          if (typeof p === "string" && p.trim()) plan = p; // last one wins
        }
      }
    }
    return NextResponse.json({ plan, mode: "" });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return bridgeError(500, "internal_error", `plan failed: ${msg}`);
  }
}

// POST /api/bridge/v1/sessions/{id}/plan — the PlanModal's decision (Phase B5).
// Resolves the pending ExitPlanMode tool call by appending a `user` tool_result
// the worker delivers to the agent, with the exact markers the ultraplan poller
// scans for (ccrSession.ts extractApprovedPlan / extractTeleportPlan):
//   approve → is_error:false, "## Approved Plan:\n<plan>" (+ edited variant)
//   reject  → is_error:true  (no sentinel → poller iterates)
//   local   → is_error:true, "__ULTRAPLAN_TELEPORT_LOCAL__\n<plan>" (run locally)
export async function POST(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params;
  const denied = await authorizeSession(req, sessionId);
  if (denied) return denied;
  const body = (await req.json().catch(() => null)) as {
    decision?: string;
    plan?: string;
    edited?: boolean;
  } | null;
  const decision = body?.decision;
  if (decision !== "approve" && decision !== "reject" && decision !== "local") {
    return bridgeError(
      400,
      "invalid_request",
      "decision must be 'approve' | 'reject' | 'local'",
    );
  }
  try {
    const store = getStore();
    const events = listSessionEvents(store, sessionId, 0);
    const toolUseId = findPendingExitPlanToolUseId(events);
    if (!toolUseId) {
      return bridgeError(409, "no_pending_plan", "No plan awaiting a decision");
    }
    const plan = typeof body?.plan === "string" ? body.plan : "";
    const edited = body?.edited === true;
    const { isError, content } = buildPlanDecision(decision, plan, edited);

    // Container sessions gate ExitPlanMode through a `can_use_tool` permission
    // the worker/events route recorded (ultraplan_permission) and deliberately
    // did NOT auto-approve — the plan paused for this decision. Resolve THAT
    // permission so the paused plan-mode agent continues correctly:
    //   approve → allow (the tool runs and emits "## Approved Plan:" →
    //             execute on the web) with the plan carried in updatedInput
    //   local   → deny, message = "__ULTRAPLAN_TELEPORT_LOCAL__\n<plan>"
    //             (becomes the is_error tool_result the poller scans → teleport
    //             the plan back to the terminal, and the remote agent stops)
    //   reject  → deny, message = "Plan rejected by user." (no marker → the
    //             poller iterates and the agent revises)
    // Browser-driven local sessions have no such event → fall through to the
    // legacy tool_result injection below (unchanged).
    const perm = findUnresolvedPlanPermission(events, toolUseId);
    if (perm) {
      // approve → allow, replaying the original input (plan read from disk);
      // only when the user edited the plan do we override input.plan so the
      // tool emits it as the "(edited by user)" variant. local/reject → deny,
      // with the decision content as the message (becomes the is_error
      // tool_result the poller scans for the teleport sentinel / rejection).
      //
      // local ALSO sets interrupt:true — the CLI's deny+interrupt protocol
      // (PermissionPromptToolResultSchema → abortController.abort()) emits the
      // sentinel tool_result and then STOPS the agent's turn. Without it the
      // plan-mode agent treats the deny as a revision request and re-calls
      // ExitPlanMode within seconds (live capture 2026-07-09: 3.5s), so the
      // remote keeps working in parallel and the new pending call shadows the
      // teleport tool_result in the CLI poller — the plan never returns to the
      // terminal. reject must NOT interrupt: the agent has to stay alive to
      // revise the plan.
      const response =
        decision === "approve"
          ? {
              behavior: "allow" as const,
              updatedInput:
                edited && plan.trim() ? { ...perm.input, plan } : perm.input,
            }
          : {
              behavior: "deny" as const,
              message: content,
              ...(decision === "local" ? { interrupt: true } : {}),
            };
      appendInbound(store, sessionId, {
        type: "control_response",
        uuid: randomBytes(8).toString("hex"),
        response: {
          subtype: "success",
          request_id: perm.requestId,
          response,
        },
      });
      appendSessionEvent(store, sessionId, {
        type: "ultraplan_permission_resolved",
        payload: { request_id: perm.requestId },
      });
      emitInbound(sessionId);
      return NextResponse.json({ ok: true });
    }

    appendInbound(store, sessionId, {
      type: "user",
      uuid: randomBytes(8).toString("hex"),
      session_id: sessionId,
      parent_tool_use_id: null,
      message: {
        role: "user",
        content: [
          {
            type: "tool_result",
            tool_use_id: toolUseId,
            is_error: isError,
            content,
          },
        ],
      },
    });
    return NextResponse.json({ ok: true });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return bridgeError(500, "internal_error", `plan decision failed: ${msg}`);
  }
}
