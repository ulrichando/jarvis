import { NextResponse } from "next/server";
import { getStore } from "@/lib/bridge/db";
import { findRoutine, listRoutineRuns } from "@/lib/bridge/store";
import { getUserId } from "@/lib/auth-helpers";
import { bridgeError } from "@/lib/bridge/errors";

// GET /api/bridge/v1/routines/{id}/runs — the routine's past runs (sessions it
// spawned), newest first, for the routine detail's run list.
export async function GET(
  req: Request,
  ctx: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const { id } = await ctx.params;
  try {
    const store = getStore();
    // Ownership: only the routine's owner may list its runs (the list discloses
    // spawned session ids). proxy.ts's /api/* bearer gate is a single shared
    // token and can't establish per-routine ownership — this is the IDOR check.
    const routine = findRoutine(store, id);
    if (!routine) return bridgeError(404, "not_found", "Routine not found");
    const userId = await getUserId(req.headers);
    if (routine.user_id && routine.user_id !== userId) {
      // No valid session against an owned routine → 401 (re-login); a real
      // cross-user mismatch still 403s.
      if (userId === null) {
        return bridgeError(401, "unauthenticated", "Session expired — please sign in again");
      }
      return bridgeError(403, "forbidden", "Not your routine");
    }
    const runs = listRoutineRuns(store, id).map((s) => ({
      session_id: s.session_id,
      created_at: s.created_at,
      archived: !!s.archived,
    }));
    return NextResponse.json({ runs });
  } catch (err) {
    return bridgeError(500, "internal_error", `runs: ${String(err)}`);
  }
}
