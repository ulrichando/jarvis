import { and, eq } from "drizzle-orm";
import { hashPassword } from "better-auth/crypto";
import { db, schema } from "./db";

// Shared helper: set (or create) the credential account row for a user and
// revoke their existing sessions. Used by:
//   • src/app/api/auth/reset/complete/route.ts  — redeem a password-reset token
//   • src/web/scripts/account.ts               — CLI emergency reset-password
//
// The function is intentionally not exported from a "server-only" module so
// the CLI script (which is NOT a Next.js server component) can import it
// without hitting the server-only boundary guard.

/**
 * Hash `newPassword` (scrypt, better-auth format), write it to the user's
 * `credential` account row (creating the row if absent), then invalidate all
 * existing sessions for that user.
 *
 * @param userId   UUID of the user whose password to set.
 * @param newPassword  Plaintext new password (caller must validate length).
 * @throws if `db` is null (DATABASE_URL not set).
 */
export async function setCredentialPassword(
  userId: string,
  newPassword: string,
): Promise<void> {
  if (!db) {
    throw new Error("DATABASE_URL is required — database not initialised");
  }

  const hashed = await hashPassword(newPassword);

  // Update existing credential account; insert if the user has none (e.g.
  // OAuth-only account adding a password for the first time — same branch
  // better-auth's own resetPassword takes).
  const updated = await db
    .update(schema.accounts)
    .set({ password: hashed, updatedAt: new Date() })
    .where(
      and(
        eq(schema.accounts.userId, userId),
        eq(schema.accounts.providerId, "credential"),
      ),
    )
    .returning({ id: schema.accounts.id });

  if (updated.length === 0) {
    await db.insert(schema.accounts).values({
      userId,
      providerId: "credential",
      accountId: userId,
      password: hashed,
    });
  }

  // Revoke all sessions so a thief holding an old cookie is logged out.
  await db.delete(schema.sessions).where(eq(schema.sessions.userId, userId));
}
