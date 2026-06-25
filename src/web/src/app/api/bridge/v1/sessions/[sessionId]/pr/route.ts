import { NextResponse } from "next/server";
import { getStore } from "@/lib/bridge/db";
import { createContainerPR } from "@/lib/bridge/containers";
import { findEnvironment, findSession } from "@/lib/bridge/store";
import { extractBearer } from "@/lib/bridge/auth";
import { getUserId } from "@/lib/auth-helpers";
import { bridgeError } from "@/lib/bridge/errors";

// Authorize a mutation on a session two ways: the CLI worker presents a bearer
// (v1-permissive — any non-empty token); the /code browser presents a
// same-origin session cookie, checked against the session's owning
// environment. Returns an error response, or null when allowed. Mirrors
// sessions/[sessionId]/route.ts (the network bearer gate in proxy.ts is a
// single shared token, so it does NOT establish per-session ownership — this
// is the IDOR check the bearer gate can't do).
async function authorizeMutation(
  req: Request,
  sessionId: string,
): Promise<NextResponse | null> {
  if (extractBearer(req.headers.get("authorization"))) return null;
  const store = getStore();
  const session = findSession(store, sessionId);
  if (!session) return bridgeError(404, "not_found", "Session not found");
  const env = session.environment_id
    ? findEnvironment(store, session.environment_id)
    : null;
  const userId = await getUserId(req.headers);
  if (env?.user_id && env.user_id !== userId) {
    // No valid session against a real-owned session → 401 (re-login); a real
    // cross-user mismatch still 403s.
    if (userId === null) {
      return bridgeError(401, "unauthenticated", "Session expired — please sign in again");
    }
    return bridgeError(403, "forbidden", "Not your session");
  }
  return null;
}

// POST /api/bridge/v1/sessions/{id}/pr — open (or find) a pull request for the
// session's work, the claude.ai/code "Create PR" action. Commits + pushes the
// session branch and runs `gh pr create` inside the container. Returns the PR
// URL (or a GitHub compare URL when gh is unavailable).
export async function POST(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params;
  const denied = await authorizeMutation(req, sessionId);
  if (denied) return denied;
  const body = (await req.json().catch(() => null)) as { mode?: string } | null;
  const mode =
    body?.mode === "draft" || body?.mode === "compose" ? body.mode : "full";
  try {
    const result = await createContainerPR(getStore(), sessionId, undefined, mode);
    if ("error" in result) {
      return bridgeError(400, "invalid_request", result.error);
    }
    return NextResponse.json(result);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return bridgeError(500, "internal_error", `PR failed: ${msg}`);
  }
}
