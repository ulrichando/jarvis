import { eq } from "drizzle-orm";
import { db, schema } from "@/lib/db";
import { getUserId } from "@/lib/auth-helpers";
import { verifyProxyToken } from "@/lib/bridge/proxyJwt";
import { getOrCreateProxyJwtSecret } from "@/lib/bridge/proxySecret";

// Shared auth for the LiveKit voice agent's cloud routes (/api/voice-memory,
// /api/memory). Extracted verbatim from /api/voice-memory/route.ts so both
// routes enforce the identical dual-auth contract.
//
// Auth resolution order:
//   1. better-auth session cookie (getUserId) → user_id = the session user.
//   2. The voice-agent SERVICE token: a proxy JWT (signProxyToken /
//      verifyProxyToken, HS256 with the shared keys.env secret) whose
//      sub === "voice-agent". MANDATORY sub check — a valid proxy JWT minted
//      for any OTHER sub (e.g. a per-user gateway token) is 403, never a
//      user-impersonation path. For the service path user_id comes from the
//      request (query param on GET, body on POST) and is validated as a UUID
//      (400) that exists in web.users (404) via resolveServiceUserId.
//   3. Neither → 401.

const SERVICE_SUB = "voice-agent";
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export type AuthResult =
  | { kind: "session"; userId: string }
  | { kind: "service" }
  | { kind: "forbidden" }
  | { kind: "none" };

export async function resolveAuth(req: Request): Promise<AuthResult> {
  const sessionUserId = await getUserId(req.headers);
  if (sessionUserId) return { kind: "session", userId: sessionUserId };
  const authz = req.headers.get("authorization") ?? "";
  const bearer = /^bearer /i.test(authz) ? authz.slice(7).trim() : "";
  if (bearer) {
    const result = verifyProxyToken(bearer, getOrCreateProxyJwtSecret());
    if (result.ok) {
      // Valid signature but wrong principal — explicitly forbidden (403),
      // NOT a fall-through to 401: per-user gateway tokens must never reach
      // the arbitrary-user_id service path.
      if (result.claims.sub !== SERVICE_SUB) return { kind: "forbidden" };
      return { kind: "service" };
    }
  }
  return { kind: "none" };
}

/** Service-path user_id: syntactic UUID check (400) + existence (404). */
export async function resolveServiceUserId(
  raw: string | null | undefined,
): Promise<{ userId: string } | { error: Response }> {
  const candidate = typeof raw === "string" ? raw.trim() : "";
  if (!candidate || !UUID_RE.test(candidate)) {
    return {
      error: Response.json(
        { error: "user_id must be a valid UUID" },
        { status: 400 },
      ),
    };
  }
  const rows = await db!
    .select({ id: schema.users.id })
    .from(schema.users)
    .where(eq(schema.users.id, candidate))
    .limit(1);
  if (rows.length === 0) {
    return {
      error: Response.json({ error: "unknown user_id" }, { status: 404 }),
    };
  }
  return { userId: candidate };
}
