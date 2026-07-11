import { NextResponse } from "next/server";
import { eq } from "drizzle-orm";
import { db, schema } from "@/lib/db";
import { getStore } from "@/lib/bridge/db";
import { resolveBridgeToken, revokeRemoteControlToken } from "@/lib/bridge/store";
import { extractBearer } from "@/lib/bridge/auth";
import { bridgeError } from "@/lib/bridge/errors";

export const runtime = "nodejs";

/**
 * POST /api/bridge/logout — cross-surface "sign out everywhere".
 *
 * The desktop tray's Sign-out (and any on-box client) POSTs here with its
 * Remote Control bridge token so signing out on one surface cascades to all:
 *   1. Deletes ALL of the user's better-auth login sessions (Postgres) → every
 *      browser / device lands on /login on its next request.
 *   2. Revokes the user's UNNAMED Remote Control bridge token(s) (SQLite) → the
 *      CLI / desktop / voice agent can no longer reach the bridge API, even if a
 *      stale copy of the token lingers on disk.
 *
 * Named API tokens (Settings → API Tokens) are deliberate long-lived creds and
 * are NOT revoked here. Self-auths in-handler (resolveBridgeToken), so it sits
 * on proxy.ts SELF_AUTH_POST_PATTERNS. Idempotent: a second call after the
 * token is already revoked just 401s (the token no longer resolves).
 */
export async function POST(req: Request): Promise<NextResponse> {
  const token = extractBearer(req.headers.get("authorization"));
  if (!token) return bridgeError(401, "unauthorized", "Missing bearer");
  const store = getStore();
  const userId = resolveBridgeToken(store, token);
  if (!userId) return bridgeError(401, "unauthorized", "Invalid token");

  // 1. Every browser/device: delete the user's login sessions.
  let sessionsCleared = false;
  if (db) {
    await db.delete(schema.sessions).where(eq(schema.sessions.userId, userId));
    sessionsCleared = true;
  }
  // 2. CLI / desktop / voice: revoke the reusable Remote Control token.
  const tokensRevoked = revokeRemoteControlToken(store, userId);

  return NextResponse.json({ ok: true, sessionsCleared, tokensRevoked });
}
