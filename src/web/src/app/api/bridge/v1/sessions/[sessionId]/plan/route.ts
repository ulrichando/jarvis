import { NextResponse } from "next/server";
import { randomBytes } from "node:crypto";
import { getStore } from "@/lib/bridge/db";
import { appendInbound, listSessionEvents } from "@/lib/bridge/store";
import { authorizeSession } from "@/lib/bridge/authz";
import { bridgeError } from "@/lib/bridge/errors";
import {
  buildPlanDecision,
  findPendingExitPlanToolUseId,
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
    const toolUseId = findPendingExitPlanToolUseId(
      listSessionEvents(store, sessionId, 0),
    );
    if (!toolUseId) {
      return bridgeError(409, "no_pending_plan", "No plan awaiting a decision");
    }
    const plan = typeof body?.plan === "string" ? body.plan : "";
    const { isError, content } = buildPlanDecision(
      decision,
      plan,
      body?.edited === true,
    );
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
