import { NextResponse } from "next/server";
import {
  listCheckpoints,
  saveCheckpoint,
} from "@/lib/checkpoints";

export const runtime = "nodejs";
export const maxDuration = 120;

/**
 * GET  /api/workspace/[id]/checkpoint
 *      → { checkpoints: Checkpoint[] }
 *
 * POST /api/workspace/[id]/checkpoint
 *      body: { id: string, label: string }
 *      → { checkpoint }
 *      Snapshots the workspace before the upcoming turn. The chat layer
 *      calls this at the START of each user-initiated submit so the
 *      assistant message that follows has an associated rollback point.
 *
 * POST /api/workspace/[id]/checkpoint/restore
 *      body: { id: string }
 *      → { restored, deleted }
 *      Rolls back to a previous snapshot. Files in the snapshot are
 *      rewritten; files added AFTER the snapshot are deleted.
 */
export async function GET(
  _req: Request,
  ctx: RouteContext<"/api/workspace/[id]/checkpoint">,
) {
  const { id } = await ctx.params;
  const checkpoints = await listCheckpoints(id);
  return NextResponse.json({ checkpoints });
}

export async function POST(
  req: Request,
  ctx: RouteContext<"/api/workspace/[id]/checkpoint">,
) {
  const { id } = await ctx.params;
  const body = await req.json().catch(() => ({}));
  const cpId = String(body.id ?? "").trim();
  const label = String(body.label ?? "").trim() || `checkpoint-${Date.now()}`;
  if (!cpId) {
    return NextResponse.json(
      { error: "missing checkpoint id" },
      { status: 400 },
    );
  }
  try {
    const checkpoint = await saveCheckpoint({
      workspaceId: id,
      id: cpId,
      label,
    });
    return NextResponse.json({ checkpoint });
  } catch (e) {
    return NextResponse.json(
      { error: (e as Error).message },
      { status: 500 },
    );
  }
}
