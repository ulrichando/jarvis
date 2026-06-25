import "server-only";
import { requireUserId, Unauthenticated } from "./auth-helpers";

/**
 * DRY auth wrapper for API route handlers. Resolves the logged-in user id and
 * passes it to `fn`; returns 401 when there's no valid session. Use this for
 * routes whose whole body needs an authenticated user. For handlers where the
 * wrapper shape is awkward (e.g. a bearer-token alternative path), call
 * `requireUserId(req.headers)` inside a try/catch instead.
 */
export async function withUser(
  req: Request,
  fn: (uid: string) => Promise<Response>,
): Promise<Response> {
  let uid: string;
  try {
    uid = await requireUserId(req.headers);
  } catch (e) {
    if (e instanceof Unauthenticated) {
      return new Response("Unauthorized", { status: 401 });
    }
    throw e;
  }
  return fn(uid);
}
