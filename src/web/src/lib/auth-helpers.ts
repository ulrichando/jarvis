import "server-only";
import { headers as nextHeaders } from "next/headers";
import { auth } from "./auth";

// 8-hour absolute cap (OWASP). Unlike the sliding idle window, this is measured
// from session CREATION and never renews — so an actively-used session is still
// forced to re-login every 8 hours. This is what makes JARVIS actually present
// the login page each work session instead of keeping you signed in forever.
const ABSOLUTE_CAP_MS = 8 * 60 * 60 * 1000;

/**
 * Returns true if the session's creation time is within the 8-hour absolute cap.
 * Used to force re-login past 8 hours regardless of activity.
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
