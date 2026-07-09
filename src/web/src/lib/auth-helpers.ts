import "server-only";
import { headers as nextHeaders } from "next/headers";
import { auth } from "./auth";

// Absolute cap, measured from session CREATION (never renews). 30 days for this
// single-user personal box so working sessions aren't interrupted — the user
// reported being logged out constantly under the old 8-hour cap. Tighten this
// back down (e.g. 8h) if the box ever goes multi-user or is exposed beyond
// localhost.
const ABSOLUTE_CAP_MS = 30 * 24 * 60 * 60 * 1000;

/**
 * Returns true if the session's creation time is within the absolute cap (30d).
 * Used to force re-login only past the cap, regardless of activity.
 */
export function isSessionWithinAbsoluteCap(createdAt: Date): boolean {
  return Date.now() - createdAt.getTime() < ABSOLUTE_CAP_MS;
}

export class Unauthenticated extends Error {
  constructor() {
    super("Unauthenticated");
    this.name = "Unauthenticated";
  }
}

/**
 * The logged-in user's id, server-side, or null when there is no valid session.
 *
 * No silent LOCAL_USER_ID fallback: a missing/expired session returns null so
 * callers make an explicit decision (API routes → 401, pages → redirect to
 * /login). The 30-day absolute cap is enforced here too — a session older than
 * the cap is treated as unauthenticated regardless of recent activity.
 *
 * In route handlers pass `req.headers`; in server components call with no args
 * (reads next/headers).
 */
export async function getUserId(reqHeaders?: Headers): Promise<string | null> {
  const session = await auth.api.getSession({
    headers: reqHeaders ?? (await nextHeaders()),
  });
  if (!session?.user?.id) return null;
  const createdAt = session.session?.createdAt;
  if (createdAt && !isSessionWithinAbsoluteCap(new Date(createdAt))) return null;
  return session.user.id;
}

/**
 * For API routes: the user id, or throw Unauthenticated so the caller can
 * return a 401. Use `withUser` (lib/auth-route.ts) to wrap a handler, or call
 * this inside a try/catch where the handler shape makes the wrapper awkward.
 */
export async function requireUserId(reqHeaders?: Headers): Promise<string> {
  const id = await getUserId(reqHeaders);
  if (!id) throw new Unauthenticated();
  return id;
}

/**
 * Like {@link requireUserId}, but ALSO accepts the shared
 * `JARVIS_LOCAL_API_TOKEN` bearer, resolving it to the canonical single-user id
 * (`LOCAL_USER_ID`). This is the EXPLICIT, per-route opt-in that lets a trusted
 * same-box service caller — the voice agent's `web_*` tools — reach user-scoped
 * routes over loopback without a browser session cookie.
 *
 * `getUserId` intentionally stays cookie-only (no silent global fallback); only
 * routes that deliberately call THIS helper accept the shared token. The bearer
 * has already cleared the proxy gate (`proxy.ts` accepts it for every `/api/*`
 * route), so this is the in-handler half of the same check — mirroring how
 * `/api/v1/sessions` accepts `isSharedLocalToken`. The shared token is exactly
 * as privileged as the box it lives on; do NOT adopt this helper on a
 * multi-user deployment. Imports are lazy so this stays out of any module cycle.
 */
export async function requireUserIdOrSharedLocal(
  reqHeaders?: Headers,
): Promise<string> {
  const id = await getUserId(reqHeaders);
  if (id) return id;
  const { extractBearer, isSharedLocalToken } = await import("./bridge/auth");
  const token = extractBearer(reqHeaders?.get("authorization") ?? null);
  if (token && isSharedLocalToken(token)) {
    const { LOCAL_USER_ID, ensureLocalUser } = await import("./chat/persist");
    await ensureLocalUser();
    return LOCAL_USER_ID;
  }
  throw new Unauthenticated();
}
