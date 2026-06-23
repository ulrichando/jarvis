import { NextResponse } from "next/server";
import { getUserId } from "@/lib/auth-helpers";
import { getOrCreateProxyJwtSecret } from "@/lib/bridge/proxySecret";
import { signPtyToken } from "@/lib/workspace/ptyToken";

export const runtime = "nodejs";

// Short-lived: the browser mints one of these immediately before each PTY
// (re)connect, so it only needs to outlive a single handshake. 10 min absorbs
// clock skew + reconnect backoff without becoming a useful stolen credential.
const TTL_SECONDS = 600;
const WSID_RE = /^[a-z0-9-]+$/i;

/**
 * POST /api/workspace/[id]/pty-token — mint the per-session credential the
 * /code terminal presents to the PTY sidecar (scripts/pty-server.mjs) before it
 * spawns a shell. This route sits behind the same /api/* gate as every other
 * workspace route (proxy.ts: bearer + better-auth login + host allowlist), so
 * reaching it already proves an authenticated app session; getUserId only
 * records WHO for the token's `sub` (it falls back to the local user when auth
 * is disabled in dev, matching the sibling workspace routes).
 *
 * The token is scoped to this `[id]` so it can't open a shell in another
 * workspace, and verified OFFLINE by the sidecar (the web app is not on the
 * websocket path).
 */
export async function POST(
  req: Request,
  ctx: RouteContext<"/api/workspace/[id]/pty-token">,
): Promise<NextResponse> {
  const { id } = await ctx.params;
  if (!WSID_RE.test(id)) {
    return NextResponse.json({ error: "bad workspace id" }, { status: 400 });
  }
  try {
    const sub = await getUserId(req.headers);
    const secret = getOrCreateProxyJwtSecret();
    const nowS = Math.floor(Date.now() / 1000);
    const token = signPtyToken({ sub, wsid: id, ttlSeconds: TTL_SECONDS }, secret, nowS);
    return NextResponse.json({ token, expiresAt: (nowS + TTL_SECONDS) * 1000 });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}
