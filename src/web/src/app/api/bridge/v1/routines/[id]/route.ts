import { NextResponse } from "next/server";
import { getStore } from "@/lib/bridge/db";
import { deleteRoutine, findRoutine, updateRoutine } from "@/lib/bridge/store";
import { getUserId } from "@/lib/auth-helpers";
import { bridgeError } from "@/lib/bridge/errors";

// Authorize a routine mutation: the routine must be owned by the requesting
// user. proxy.ts's /api/* network bearer gate is a single SHARED token and so
// doesn't establish per-routine ownership — this is the IDOR check it can't do.
// Mirrors the routines list route (getUserId-scoped). Returns an error response
// to short-circuit on, or null when allowed. The `routine.user_id &&` guard
// keeps unowned/legacy routines (null user_id) working in single-user mode.
async function authorizeRoutine(
  req: Request,
  id: string,
): Promise<NextResponse | null> {
  const store = getStore();
  const routine = findRoutine(store, id);
  if (!routine) return bridgeError(404, "not_found", "Routine not found");
  const userId = await getUserId(req.headers);
  if (routine.user_id && routine.user_id !== userId) {
    return bridgeError(403, "forbidden", "Not your routine");
  }
  return null;
}

// PATCH /api/bridge/v1/routines/{id} — pause/resume or rename.
export async function PATCH(
  req: Request,
  ctx: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const { id } = await ctx.params;
  const body = (await req.json().catch(() => null)) as {
    paused?: boolean;
    name?: string;
    instructions?: string;
  } | null;
  if (!body) return bridgeError(400, "invalid_request", "JSON body required");
  try {
    const denied = await authorizeRoutine(req, id);
    if (denied) return denied;
    updateRoutine(getStore(), id, {
      paused: typeof body.paused === "boolean" ? body.paused : undefined,
      name: typeof body.name === "string" && body.name.trim() ? body.name.trim() : undefined,
      instructions:
        typeof body.instructions === "string" && body.instructions.trim()
          ? body.instructions.trim()
          : undefined,
    });
    return NextResponse.json({ id });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return bridgeError(500, "internal_error", `DB error: ${msg}`);
  }
}

// DELETE /api/bridge/v1/routines/{id}
export async function DELETE(
  req: Request,
  ctx: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const { id } = await ctx.params;
  try {
    const denied = await authorizeRoutine(req, id);
    if (denied) return denied;
    deleteRoutine(getStore(), id);
    return new NextResponse(null, { status: 204 });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return bridgeError(500, "internal_error", `DB error: ${msg}`);
  }
}
