import { NextResponse } from "next/server";
import { getStore } from "@/lib/bridge/db";
import { findEnvironment, findSession, validateSessionToken } from "@/lib/bridge/store";
import { extractBearer } from "@/lib/bridge/auth";
import { getUserId } from "@/lib/auth-helpers";
import { LOCAL_USER_ID } from "@/lib/chat/persist";
import { bridgeError } from "@/lib/bridge/errors";

// Shared session-ownership gate for bridge routes. The CLI worker presents a
// bearer (v1-permissive — any non-empty token); the browser presents a
// same-origin session cookie, checked against the session's owning environment.
//
// Why per-route: proxy.ts's /api/* network bearer gate is a SINGLE shared token,
// so it authenticates "a trusted local caller" but can't establish WHICH user —
// it cannot do the per-session ownership (IDOR) check. This can.
//
// Returns an error response to short-circuit on, or null when allowed. Mirrors
// the inline `authorizeMutation` in sessions/[sessionId]/route.ts; the
// messages route's 401-vs-403 nuance (lapsed session → re-login) is preserved.
export async function authorizeSession(
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
    // A lapsed session resolves to LOCAL_USER_ID (the getUserId fallback). When
    // the session is owned by a real account, that's "your login expired" → 401
    // so the client re-logs in, not a dead-end 403. A genuine cross-user
    // mismatch (two real accounts) still returns 403.
    if (userId === LOCAL_USER_ID && env.user_id !== LOCAL_USER_ID) {
      return bridgeError(401, "unauthenticated", "Session expired — please sign in again");
    }
    return bridgeError(403, "forbidden", "Not your session");
  }
  return null;
}

// Worker session-token gate for the CCR v2 /code worker routes: the bearer is
// the per-session ingress token minted at session creation. Extracted from the
// 7 worker routes that each repeated this exact check (2 as a local `authorize`,
// 5 inlined). Returns an error response to short-circuit on, or null when
// allowed. Distinct from authorizeSession above (the browser cookie-ownership
// gate); this is the token-presenting worker's gate.
export function authorizeSessionToken(
  req: Request,
  sessionId: string,
): NextResponse | null {
  const token = extractBearer(req.headers.get("authorization"));
  if (!token) return bridgeError(401, "unauthorized", "Missing bearer");
  if (!validateSessionToken(getStore(), sessionId, token)) {
    return bridgeError(401, "unauthorized", "Invalid session token");
  }
  return null;
}
