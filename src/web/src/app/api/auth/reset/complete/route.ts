import { and, eq, gt, isNull } from "drizzle-orm";
import { hashPassword } from "better-auth/crypto";
import { db, schema } from "@/lib/db";

export const runtime = "nodejs";

// POST /api/auth/reset/complete  — body: { token, password }
//
// Step 3: redeem the single-use token from /reset/verify and set a new
// password. Mirrors better-auth's own resetPassword internals exactly (verified
// by reading dist/api/routes/password.mjs):
//   • hash = hashPassword(newPassword)            — scrypt "salt:hex", the same
//     format the credential sign-in verifier reads.
//   • write it to the `credential` account row (insert one if the user somehow
//     has none, e.g. an OAuth-only account adding a password).
//   • revoke the user's sessions (delete `sessions` rows) so a thief holding an
//     old cookie is logged out — better-auth's revokeSessionsOnPasswordReset.
export async function POST(req: Request) {
  const body = (await req.json().catch(() => null)) as {
    token?: unknown;
    password?: unknown;
  } | null;
  const token = typeof body?.token === "string" ? body.token : "";
  const password = typeof body?.password === "string" ? body.password : "";

  if (!db) return new Response("Persistence disabled", { status: 503 });
  if (!token) return Response.json({ error: "invalid token" }, { status: 400 });
  if (password.length < 8) {
    return Response.json(
      { error: "password must be at least 8 characters" },
      { status: 400 },
    );
  }

  // Look up an UNUSED, UNEXPIRED token. The unique constraint on `token` makes
  // this a point lookup.
  const now = new Date();
  const [reset] = await db
    .select({ id: schema.passwordResets.id, userId: schema.passwordResets.userId })
    .from(schema.passwordResets)
    .where(
      and(
        eq(schema.passwordResets.token, token),
        isNull(schema.passwordResets.usedAt),
        gt(schema.passwordResets.expiresAt, now),
      ),
    )
    .limit(1);
  if (!reset) {
    return Response.json({ error: "invalid token" }, { status: 400 });
  }

  // Atomically claim the token: only one redemption wins the race. If another
  // request already marked it used between our SELECT and here, the conditional
  // UPDATE affects 0 rows and we bail without touching the password.
  const claimed = await db
    .update(schema.passwordResets)
    .set({ usedAt: now })
    .where(
      and(
        eq(schema.passwordResets.id, reset.id),
        isNull(schema.passwordResets.usedAt),
      ),
    )
    .returning({ id: schema.passwordResets.id });
  if (claimed.length === 0) {
    return Response.json({ error: "invalid token" }, { status: 400 });
  }

  const hashed = await hashPassword(password);

  // Update the credential account's password; create one if absent (same branch
  // better-auth's resetPassword takes for password-less accounts).
  const updated = await db
    .update(schema.accounts)
    .set({ password: hashed, updatedAt: new Date() })
    .where(
      and(
        eq(schema.accounts.userId, reset.userId),
        eq(schema.accounts.providerId, "credential"),
      ),
    )
    .returning({ id: schema.accounts.id });
  if (updated.length === 0) {
    await db.insert(schema.accounts).values({
      userId: reset.userId,
      providerId: "credential",
      accountId: reset.userId,
      password: hashed,
    });
  }

  // Revoke existing sessions so any stolen/stale cookie is dead.
  await db
    .delete(schema.sessions)
    .where(eq(schema.sessions.userId, reset.userId));

  return Response.json({ ok: true });
}
