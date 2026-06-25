import { NextResponse } from "next/server";
import { getStore } from "@/lib/bridge/db";
import { findRoutine, type RoutineTrigger } from "@/lib/bridge/store";
import { runRoutine } from "@/lib/bridge/routines-run";
import { getUserId } from "@/lib/auth-helpers";
import { bridgeError } from "@/lib/bridge/errors";

// POST /api/bridge/v1/routines/{id}/run — fire a routine now.
//   - From the UI ("Run now"): session-cookie, no body.
//   - As an API/webhook trigger: send { token } matching the routine's api
//     trigger token (so the webhook URL is the auth — no cookie needed).
export async function POST(
  req: Request,
  ctx: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const { id } = await ctx.params;
  const body = (await req.json().catch(() => null)) as { token?: string } | null;
  try {
    const store = getStore();
    const routine = findRoutine(store, id);
    if (!routine) return bridgeError(404, "not_found", "Routine not found");

    // Auth: an API-trigger token (the webhook URL itself is the auth) OR — for
    // the UI "Run now" with no token — the requesting user must OWN the routine.
    // proxy.ts's /api/* bearer gate is a single shared token and can't establish
    // per-routine ownership; this is the IDOR check it can't do.
    if (body?.token) {
      let trig: RoutineTrigger | null = null;
      try {
        trig = JSON.parse(routine.trigger_json) as RoutineTrigger;
      } catch {
        trig = null;
      }
      if (trig?.type !== "api" || trig.token !== body.token) {
        return bridgeError(401, "unauthorized", "Invalid routine token");
      }
    } else {
      const userId = await getUserId(req.headers);
      if (routine.user_id && routine.user_id !== userId) {
        // No valid session against an owned routine → 401 (re-login); a real
        // cross-user mismatch still 403s.
        if (userId === null) {
          return bridgeError(401, "unauthenticated", "Session expired — please sign in again");
        }
        return bridgeError(403, "forbidden", "Not your routine");
      }
    }

    const origin = new URL(req.url).origin;
    const result = await runRoutine(store, routine, origin);
    if ("error" in result) {
      return bridgeError(400, "invalid_request", result.error);
    }
    return NextResponse.json({ session_id: result.sessionId }, { status: 200 });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return bridgeError(500, "internal_error", `DB error: ${msg}`);
  }
}
