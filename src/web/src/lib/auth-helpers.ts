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
