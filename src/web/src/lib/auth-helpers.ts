import "server-only";
import { headers as nextHeaders } from "next/headers";
import { auth } from "./auth";
import { LOCAL_USER_ID } from "./chat/persist";

/**
 * The logged-in user's id, server-side. In route handlers pass `req.headers`;
 * in server components call with no args (reads next/headers). Falls back to
 * LOCAL_USER_ID when there's no session (auth-disabled / dev) so data routes
 * keep working without a login.
 */
export async function getUserId(reqHeaders?: Headers): Promise<string> {
  try {
    const session = await auth.api.getSession({
      headers: reqHeaders ?? (await nextHeaders()),
    });
    if (session?.user?.id) return session.user.id;
  } catch (err) {
    // getSession() THROWS only on a real backend error — a missing/!matched
    // cookie returns null (handled above), not a throw. So this branch is never
    // the normal logged-out path; swallowing it silently is what made the /code
    // "session lapse" invisible (it degrades to LOCAL_USER_ID, and the
    // per-session ownership check then 403s the real owner out of their own
    // sessions). Surface it so the actual cause is diagnosable.
    console.error("[auth] getSession failed; falling back to LOCAL_USER_ID:", err);
  }
  return LOCAL_USER_ID;
}
