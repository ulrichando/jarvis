import { NextResponse } from "next/server";
import { getStore } from "@/lib/bridge/db";
import { getContainerDiff } from "@/lib/bridge/containers";
import { bridgeError } from "@/lib/bridge/errors";

// GET /api/bridge/v1/sessions/{id}/diff — what the agent changed in the
// session's container (the claude.ai/code "review the diff" view). Read-only;
// returns an empty diff (not an error) when there is no container or no change
// yet, so the panel can poll without flapping error states.
export async function GET(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params;
  const summaryOnly = new URL(req.url).searchParams.get("summary") === "1";
  try {
    const result = await getContainerDiff(getStore(), sessionId, undefined, summaryOnly);
    if ("error" in result) {
      return NextResponse.json({ branch: "", base: "", ahead: 0, stat: "", diff: "" });
    }
    return NextResponse.json(result);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return bridgeError(500, "internal_error", `diff failed: ${msg}`);
  }
}
