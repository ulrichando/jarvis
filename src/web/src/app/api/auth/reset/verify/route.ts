import { eq } from "drizzle-orm";
import { db, schema } from "@/lib/db";
import { consumeBackupCode, verifyTotpForUser } from "@/lib/auth-totp";
import { rateLimitAllow } from "@/lib/reset-ratelimit";

export const runtime = "nodejs";

// Generic failure — identical shape/status whether the email is unknown, the
// user has no 2FA, or the code is simply wrong. No user enumeration.
const FAIL = () =>
  Response.json({ error: "invalid code" }, { status: 401 });

// POST /api/auth/reset/verify  — body: { email, code }
//
// Step 2: the user proves identity with a current 6-digit TOTP code OR a
// one-time backup code from the authenticator they enrolled. On success we mint
// a short-lived, single-use reset token (stored in `password_resets`) that
// /reset/complete redeems to set a new password. There is no email round-trip.
//
// Defenses:
//   • Rate limit: max 5 attempts / 15 min per EMAIL (not IP — X-Forwarded-For
//     is attacker-forgeable, and on a loopback app every request is 127.0.0.1).
//     Per-account limiting blunts brute force of the 6-digit code (1e6 space)
//     regardless of how many origins the attacker rotates through.
//   • No enumeration: unknown email, no 2FA, and wrong code all return the same
//     401 generic failure.
//   • Token: random UUID, 10-minute expiry, single-use (enforced at /complete).
export async function POST(req: Request) {
  const body = (await req.json().catch(() => null)) as {
    email?: unknown;
    code?: unknown;
  } | null;
  const email =
    typeof body?.email === "string" ? body.email.trim().toLowerCase() : "";
  const code = typeof body?.code === "string" ? body.code.trim() : "";

  if (!db) return FAIL();

  // Rate-limit FIRST (counts this attempt). Keyed by EMAIL ALONE — deliberately
  // NOT by client IP: X-Forwarded-For / X-Real-IP are attacker-forgeable, so an
  // IP dimension would let a single attacker rotate the header to bypass the
  // budget and brute-force the 6-digit code. Per-account limiting is the real
  // defense: 5 attempts / 15 min for a given email, regardless of origin. 429
  // leaks nothing about account existence (purely this email's own request rate).
  const key = email || "unknown";
  if (!rateLimitAllow(key)) {
    return Response.json({ error: "too many attempts" }, { status: 429 });
  }

  if (!email || !code) return FAIL();

  // Resolve the user. Missing user → same generic failure as a wrong code.
  let userId: string | null = null;
  try {
    const [user] = await db
      .select({ id: schema.users.id })
      .from(schema.users)
      .where(eq(schema.users.email, email))
      .limit(1);
    userId = user?.id ?? null;
  } catch {
    return FAIL();
  }
  if (!userId) return FAIL();

  // A correct TOTP OR an unused backup code authorizes the reset. Backup-code
  // consumption is single-use and persists the remaining codes.
  let ok = false;
  try {
    ok =
      (await verifyTotpForUser(userId, code)) ||
      (await consumeBackupCode(userId, code));
  } catch {
    return FAIL();
  }
  if (!ok) return FAIL();

  // Mint a single-use, 10-minute reset token.
  const token = crypto.randomUUID();
  const expiresAt = new Date(Date.now() + 10 * 60 * 1000);
  try {
    await db.insert(schema.passwordResets).values({
      userId,
      token,
      expiresAt,
    });
  } catch {
    return FAIL();
  }

  return Response.json({ token });
}
