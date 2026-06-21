import { NextResponse } from "next/server";
import { getStore } from "@/lib/bridge/db";
import { findEnvironment, deleteEnvironment } from "@/lib/bridge/store";
import { getUserId } from "@/lib/auth-helpers";
import { bridgeError } from "@/lib/bridge/errors";

// DELETE /api/bridge/v1/environments/{envId} — archive (remove) an environment
// from the /code picker. Owner-scoped. Powers the "Archive" action in the
// Update cloud environment modal (claude.ai/code parity).
export async function DELETE(
  req: Request,
  ctx: { params: Promise<{ envId: string }> },
): Promise<NextResponse> {
  const { envId } = await ctx.params;
  const env = findEnvironment(getStore(), envId);
  if (!env) return bridgeError(404, "not_found", "Environment not found");
  const userId = await getUserId(req.headers);
  // Owner-scoped: a definite ownership match is required (null-owner refused).
  if (env.user_id !== userId) {
    return bridgeError(403, "forbidden", "Not your environment");
  }
  deleteEnvironment(getStore(), envId);
  return NextResponse.json({ ok: true });
}
