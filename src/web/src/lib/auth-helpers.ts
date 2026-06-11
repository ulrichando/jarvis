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
  } catch {
    /* no session — fall through */
  }
  return LOCAL_USER_ID;
}
