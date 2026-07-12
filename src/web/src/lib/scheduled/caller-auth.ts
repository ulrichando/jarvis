import "server-only";

import { timingSafeEqual } from "node:crypto";
import {
  requireUserId,
  Unauthenticated,
  resolveSharedLocalOwnerId,
} from "@/lib/auth-helpers";

// Resolve the caller of a scheduled-tasks API request to an owning user id —
// accepting EITHER a logged-in browser session OR the shared voice token
// (JARVIS_SCHEDULED_VOICE_TOKEN) used by the headless JARVIS voice `scheduled`
// tool. Returns the owner userId, or null if neither authenticates.
//
// The token path (constant-time, fail-closed) resolves to the box owner — the
// same single-user resolution the poller endpoint uses. Only the read (list)
// and run-now routes accept the token; create/edit/delete stay session-only.
export async function resolveScheduledCaller(
  req: Request,
): Promise<string | null> {
  // 1. Browser session (the web UI).
  try {
    return await requireUserId(req.headers);
  } catch (e) {
    if (!(e instanceof Unauthenticated)) throw e;
  }
  // 2. Shared voice token — the JARVIS voice tool has no session.
  const expected = process.env.JARVIS_SCHEDULED_VOICE_TOKEN;
  if (!expected) return null;
  const auth = req.headers.get("authorization") ?? "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7).trim() : "";
  const a = Buffer.from(token);
  const b = Buffer.from(expected);
  if (a.length !== b.length || !timingSafeEqual(a, b)) return null;
  return await resolveSharedLocalOwnerId();
}
