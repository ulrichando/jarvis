import { NextResponse } from "next/server";
import { getStore } from "@/lib/bridge/db";
import { listSessionEvents } from "@/lib/bridge/store";
import { authorizeSession } from "@/lib/bridge/authz";
import { bridgeError } from "@/lib/bridge/errors";

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
