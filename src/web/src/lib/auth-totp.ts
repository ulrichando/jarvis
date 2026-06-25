import { authenticator } from "otplib";
import { symmetricDecrypt, symmetricEncrypt } from "better-auth/crypto";
import { eq } from "drizzle-orm";
import { db, schema } from "./db";

// Standalone TOTP / backup-code verification against a user's enrolled
// `two_factors` row, WITHOUT a better-auth session. Used by the password-reset
// flow: the user proves identity with a 6-digit authenticator code (or a
// one-time backup code) instead of an email link.
//
// How the better-auth `twoFactor` plugin (v1.6.16) stores credentials — verified
// by reading node_modules/better-auth/dist/plugins/two-factor/* and
// dist/crypto/index.mjs:
//   • `secret`      — the base32 TOTP secret, encrypted via
//                     symmetricEncrypt({ key: secretConfig, data: base32 }).
//   • `backupCodes` — JSON.stringify(string[]) of "xxxxx-xxxxx" codes, then
//                     symmetricEncrypt'd (the plugin defaults
//                     backupCodeOptions.storeBackupCodes = "encrypted").
//   • `secretConfig`— equals `BETTER_AUTH_SECRET` for this app (no
//                     BETTER_AUTH_SECRETS array → a plain-string key →
//                     bare-hex xchacha20poly1305 ciphertext). We pass the same
//                     key so symmetricDecrypt round-trips.
// Using better-auth's OWN symmetricDecrypt (not a hand-rolled cipher) means the
// crypto can never silently drift from however the plugin encrypted the row.

function secretKey(): string {
  // Mirror better-auth's context: secretConfig === options.secret ===
  // BETTER_AUTH_SECRET (||AUTH_SECRET) when no BETTER_AUTH_SECRETS rotation
  // array is configured (auth.ts passes only `secret`).
  const key =
    process.env.BETTER_AUTH_SECRET ?? process.env.AUTH_SECRET ?? "";
  if (!key) {
    throw new Error(
      "BETTER_AUTH_SECRET is required to verify TOTP / backup codes",
    );
  }
  return key;
}

async function loadTwoFactor(
  userId: string,
): Promise<{ id: string; secret: string; backupCodes: string } | null> {
  if (!db) return null;
  const rows = await db
    .select({
      id: schema.twoFactors.id,
      secret: schema.twoFactors.secret,
      backupCodes: schema.twoFactors.backupCodes,
    })
    .from(schema.twoFactors)
    .where(eq(schema.twoFactors.userId, userId))
    .limit(1);
  return rows[0] ?? null;
}

/**
 * True iff `code` is a valid current TOTP for the user's enrolled secret.
 * False for a wrong code, a user with no enrollment, or any decrypt error.
 */
export async function verifyTotpForUser(
  userId: string,
  code: string,
): Promise<boolean> {
  const trimmed = (code ?? "").trim();
  if (!trimmed) return false;
  const row = await loadTwoFactor(userId);
  if (!row) return false;
  let secret: string;
  try {
    secret = await symmetricDecrypt({ key: secretKey(), data: row.secret });
  } catch {
    // Corrupt/rotated ciphertext — treat as a failed verification, never throw
    // into the caller (which would leak a different error path / timing).
    return false;
  }
  try {
    // otplib default window is the current 30s step; that matches the plugin's
    // createOTP(...).verify (period 30, digits 6).
    return authenticator.verify({ token: trimmed, secret });
  } catch {
    return false;
  }
}

// Length-independent constant-time-ish string compare, so backup-code matching
// doesn't leak which code (or how much of it) was correct via timing. We still
// must scan every stored code (no early exit on first match) for the same
// reason.
function timingSafeEqual(a: string, b: string): boolean {
  let diff = a.length ^ b.length;
  const max = Math.max(a.length, b.length);
  for (let i = 0; i < max; i++) {
    diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  }
  return diff === 0;
}

/**
 * If `code` matches one of the user's remaining backup codes, consume it
 * (remove it, persist the rest) and return true. Each code is single-use. A
 * non-match, unknown user, or decrypt error returns false without mutating.
 */
export async function consumeBackupCode(
  userId: string,
  code: string,
): Promise<boolean> {
  const trimmed = (code ?? "").trim();
  if (!trimmed) return false;
  if (!db) return false;
  const row = await loadTwoFactor(userId);
  if (!row) return false;

  let codes: string[];
  try {
    const json = await symmetricDecrypt({
      key: secretKey(),
      data: row.backupCodes,
    });
    const parsed = JSON.parse(json);
    if (!Array.isArray(parsed)) return false;
    codes = parsed.filter((c): c is string => typeof c === "string");
  } catch {
    return false;
  }

  // Scan ALL codes (constant-time compare, no early exit) so neither a match
  // nor its position is observable via timing.
  let matched = false;
  const remaining: string[] = [];
  for (const stored of codes) {
    if (timingSafeEqual(stored, trimmed)) {
      matched = true; // drop this one (single-use)
    } else {
      remaining.push(stored);
    }
  }
  if (!matched) return false;

  const reencrypted = await symmetricEncrypt({
    key: secretKey(),
    data: JSON.stringify(remaining),
  });
  await db
    .update(schema.twoFactors)
    .set({ backupCodes: reencrypted })
    .where(eq(schema.twoFactors.id, row.id));
  return true;
}
