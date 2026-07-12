import "server-only";
import { headers as nextHeaders } from "next/headers";
import { auth } from "./auth";

// Absolute cap, measured from session CREATION (never renews). 7 days: even a
// continuously-active session (which slides the 8-hour idle window forward
// indefinitely) is force-expired weekly, bounding a hijacked/stolen cookie.
// Paired with the 8-hour idle window in auth.ts. The old 30-day cap made the
// login feel like it never expired; the old 8-hour cap logged the user out
// constantly — 7 days is the middle. Tighten if the box ever goes multi-user
// or is exposed beyond localhost.
const ABSOLUTE_CAP_MS = 7 * 24 * 60 * 60 * 1000;

/**
 * Returns true if the session's creation time is within the absolute cap (7d).
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
export async function getUserIdOrSharedLocal(
  reqHeaders?: Headers,
): Promise<string | null> {
  const id = await getUserId(reqHeaders);
  if (id) return id;
  const { extractBearer, isSharedLocalToken } = await import("./bridge/auth");
  const token = extractBearer(reqHeaders?.get("authorization") ?? null);
  if (!token) return null;
  if (isSharedLocalToken(token)) {
    return resolveSharedLocalOwnerId();
  }
  // Per-user API / bridge token — the `jbr_` tokens minted at Settings → API
  // Tokens (and `jarvis auth login --token`) are bridge_tokens rows, so they
  // resolve to their owning user. Lets a service caller (the voice agent's
  // web_* tools driving a remote web) authenticate as that user with a token
  // they hold, scoped to their OWN data. Requires the route to sit on proxy.ts
  // SELF_AUTH so the token reaches this handler — same shape as the CCR routes.
  const { getStore } = await import("./bridge/db");
  const { resolveBridgeToken } = await import("./bridge/store");
  return resolveBridgeToken(getStore(), token) ?? null;
}

/** Throwing variant of {@link getUserIdOrSharedLocal} for try/catch handlers. */
export async function requireUserIdOrSharedLocal(
  reqHeaders?: Headers,
): Promise<string> {
  const id = await getUserIdOrSharedLocal(reqHeaders);
  if (!id) throw new Unauthenticated();
  return id;
}

/**
 * The box owner's user id for a shared-token (service) caller — the identity
 * whose data the browser UI actually shows.
 *
 * The web app migrates `LOCAL_USER_ID` data to the FIRST real login (see the
 * databaseHooks.user.create.after migration in `auth.ts`), so the live owner is
 * the single real (non-local) user once anyone has logged in, and the synthetic
 * `LOCAL_USER_ID` only in the pre-login state. Resolving to `LOCAL_USER_ID`
 * unconditionally would point a service caller at an empty/stale silo on a
 * logged-in box — so pick the real user when there is exactly one.
 *
 * A genuinely multi-user box (2+ real users) makes the single shared token
 * ambiguous — it can't name which user — so return null (→ 401) rather than
 * guess. Single-user personal boxes (the JARVIS target) never hit that.
 *
 * Exported for trusted in-process service callers (the scheduled-chats
 * background tick) that need the owning user without a request context.
 */
export async function resolveSharedLocalOwnerId(): Promise<string | null> {
  const { LOCAL_USER_ID, ensureLocalUser } = await import("./chat/persist");
  const { db, schema } = await import("./db");
  if (!db) return LOCAL_USER_ID;
  const { ne } = await import("drizzle-orm");
  const realUsers = await db
    .select({ id: schema.users.id })
    .from(schema.users)
    .where(ne(schema.users.id, LOCAL_USER_ID))
    .limit(2);
  if (realUsers.length === 1) return realUsers[0]!.id;
  if (realUsers.length === 0) {
    await ensureLocalUser();
    return LOCAL_USER_ID;
  }
  return null; // ambiguous multi-user box → caller treats as unauthenticated
}
