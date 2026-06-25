import { and, eq } from "drizzle-orm";
import { db, schema } from "@/lib/db";

export const runtime = "nodejs";

// POST /api/auth/reset/request  — body: { email }
//
// Step 1 of the email-free, TOTP-authorized password reset. This endpoint is
// deliberately a near no-op: it ALWAYS responds `{ ok: true }` with the same
// shape and timing whether or not the email maps to a real account, so it can't
// be used to enumerate which addresses are registered. The actual proof of
// identity happens at /reset/verify (a TOTP or backup code) — there is no email
// to send and no secret returned here.
//
// We still do the work of resolving the user + checking for a verified
// `two_factors` enrollment so the timing roughly matches the verify path and so
// a future change (e.g. a short-lived challenge marker) has a hook. Nothing
// about the result is observable to the caller.
export async function POST(req: Request) {
  const body = (await req.json().catch(() => null)) as {
    email?: unknown;
  } | null;
  const email =
    typeof body?.email === "string" ? body.email.trim().toLowerCase() : "";

  // Always behave the same regardless of input validity / account existence.
  if (db && email) {
    try {
      const [user] = await db
        .select({ id: schema.users.id })
        .from(schema.users)
        .where(eq(schema.users.email, email))
        .limit(1);
      if (user) {
        // Confirm a verified TOTP enrollment exists. Result is intentionally
        // NOT surfaced — a user without 2FA simply can't complete the reset at
        // /verify, and we don't reveal that here.
        await db
          .select({ id: schema.twoFactors.id })
          .from(schema.twoFactors)
          .where(
            and(
              eq(schema.twoFactors.userId, user.id),
              eq(schema.twoFactors.verified, true),
            ),
          )
          .limit(1);
      }
    } catch {
      // Swallow — never let a DB hiccup turn into a different (enumerable)
      // response.
    }
  }

  return Response.json({ ok: true });
}
