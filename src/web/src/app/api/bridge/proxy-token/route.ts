import { NextResponse } from "next/server";
import { getStore } from "@/lib/bridge/db";
import { resolveBridgeToken } from "@/lib/bridge/store";
import { getUserId } from "@/lib/auth-helpers";
import { getOrCreateProxyJwtSecret } from "@/lib/bridge/proxySecret";
import { signProxyToken } from "@/lib/bridge/proxyJwt";

// 30 days — matches the better-auth session lifetime (lib/auth.ts). The CLI
// re-mints by re-running `jarvis auth login` (or, non-interactively, via the
// stored Remote Control bridge token — the Bearer path below).
const TTL_SECONDS = 60 * 60 * 24 * 30;

/**
 * POST /api/bridge/proxy-token — mint the local-proxy credential ("OAuth via
 * login") for the logged-in JARVIS user.
 *
 * Auth, in order of preference:
 *   1. `Authorization: Bearer jbr_…` — a valid Remote Control bridge token.
 *      This is the non-interactive refresh path: the CLI already stores this
 *      long-lived token, so it can re-mint a fresh proxy JWT without a
 *      password prompt.
 *   2. better-auth session cookie — the interactive `jarvis auth login` path.
 *      (getUserId falls back to the local user when auth is disabled, matching
 *      the sibling GET /api/bridge/token route's behavior on this self-hosted
 *      single-user box.)
 *
 * Returns a short-lived HS256 JWT that the local proxy verifies OFFLINE — the
 * web app is not on the proxy's request path.
 */
export async function POST(req: Request): Promise<NextResponse> {
  try {
    let userId: string | null = null;

    const authz = req.headers.get("authorization");
    const bearer =
      authz && /^bearer /i.test(authz) ? authz.slice(7).trim() : undefined;
    if (bearer && bearer.startsWith("jbr_")) {
      userId = resolveBridgeToken(getStore(), bearer);
      if (!userId) {
        return NextResponse.json(
          { error: "invalid or unknown bridge token" },
          { status: 401 },
        );
      }
    } else {
      userId = await getUserId(req.headers);
      if (!userId) {
        return NextResponse.json(
          { error: "authentication required" },
          { status: 401 },
        );
      }
    }

    const secret = getOrCreateProxyJwtSecret();
    const nowS = Math.floor(Date.now() / 1000);
    const token = signProxyToken(
      { sub: userId, ttlSeconds: TTL_SECONDS },
      secret,
      nowS,
    );
    return NextResponse.json({
      token,
      expiresAt: (nowS + TTL_SECONDS) * 1000,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}
