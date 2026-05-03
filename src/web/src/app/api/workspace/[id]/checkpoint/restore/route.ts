import { NextResponse } from "next/server";
import { restoreCheckpoint } from "@/lib/checkpoints";

export const runtime = "nodejs";
export const maxDuration = 120;

/**
 * POST /api/workspace/[id]/checkpoint/restore
 *   body: { id: string }
 *   → { restored, deleted }
 *
 * Rolls the workspace back to a saved snapshot. Files in the snapshot
 * are rewritten; files that were added AFTER the snapshot get deleted.
 * Skipped paths (node_modules, .git, .next, data/*.db) are left alone.
 */
export async function POST(
  req: Request,
  ctx: RouteContext<"/api/workspace/[id]/checkpoint/restore">,
) {
  const { id } = await ctx.params;
  const body = await req.json().catch(() => ({}));
  const cpId = String(body.id ?? "").trim();
  if (!cpId) {
    return NextResponse.json(
      { error: "missing checkpoint id" },
      { status: 400 },
    );
  }
  try {
    const result = await restoreCheckpoint({ workspaceId: id, id: cpId });
    return NextResponse.json(result);
  } catch (e) {
    return NextResponse.json(
      { error: (e as Error).message },
      { status: 500 },
    );
  }
}
