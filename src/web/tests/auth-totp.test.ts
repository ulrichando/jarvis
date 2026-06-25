import { authenticator } from "otplib";
import { symmetricEncrypt } from "better-auth/crypto";
import { beforeEach, describe, expect, it, vi } from "vitest";

// auth-totp verifies a user's enrolled TOTP / backup codes WITHOUT a session,
// reading the better-auth `two_factors` row directly. The plugin stores
// `secret` and `backupCodes` ENCRYPTED with BETTER_AUTH_SECRET (xchacha20poly1305
// via better-auth's symmetricEncrypt — confirmed by reading the plugin source).
// We exercise the REAL crypto + REAL otplib here; only the Postgres I/O (the
// `@/lib/db` module) is replaced with an in-memory fake that implements the
// exact drizzle chains the helper calls.

const SECRET = "test-better-auth-secret-0123456789abcdef";
process.env.BETTER_AUTH_SECRET = SECRET;

// A known base32 TOTP secret (the kind otplib / the enroll flow generates).
const TOTP_SECRET = authenticator.generateSecret();
const USER_ID = "11111111-1111-1111-1111-111111111111";

// ── In-memory fake of `@/lib/db` ────────────────────────────────────────────
// One mutable row store the test seeds + asserts against. The fake mimics the
// minimal drizzle query-builder surface the helper uses:
//   db.select().from(t).where(pred).limit(n)        → Promise<row[]>
//   db.update(t).set(patch).where(pred)             → Promise (mutates row)
// `where(...)` is matched structurally: the helper filters by a single column
// value, captured by our `eq` mock as { col, value }.
type TwoFactorRow = {
  id: string;
  userId: string;
  secret: string;
  backupCodes: string;
  verified: boolean;
};

const store: { rows: TwoFactorRow[] } = { rows: [] };

// Our fake `eq(column, value)` returns a descriptor the fake db can apply.
// The real schema column objects are opaque to the test, so we tag by a stable
// key the test controls. The helper only ever filters two_factors by `userId`
// (lookup) or `id` (update target); we encode which via the column proxy.
type EqPred = { __col: string; value: unknown };
function applyPred(rows: TwoFactorRow[], pred: EqPred): TwoFactorRow[] {
  return rows.filter(
    (r) => (r as unknown as Record<string, unknown>)[pred.__col] === pred.value,
  );
}

vi.mock("@/lib/db", () => {
  // Column proxy: schema.twoFactors.userId → { __col: "userId" }, etc.
  const colProxy = new Proxy(
    {},
    { get: (_t, prop: string) => ({ __col: prop }) },
  );
  const schema = {
    twoFactors: colProxy as Record<string, { __col: string }>,
  };

  const db = {
    select: () => ({
      from: () => ({
        where: (pred: EqPred) => ({
          limit: async () => applyPred(store.rows, pred),
        }),
      }),
    }),
    update: () => ({
      set: (patch: Partial<TwoFactorRow>) => ({
        where: async (pred: EqPred) => {
          for (const r of applyPred(store.rows, pred)) Object.assign(r, patch);
        },
      }),
    }),
  };

  return { db, schema };
});

// `eq(col, value)` from drizzle → our descriptor. `and(...)` not needed (helper
// filters by a single column), but stub it to passthrough the first pred.
vi.mock("drizzle-orm", async (orig) => {
  const actual = await orig<typeof import("drizzle-orm")>();
  return {
    ...actual,
    eq: (col: { __col: string }, value: unknown): EqPred => ({
      __col: col.__col,
      value,
    }),
  };
});

import { consumeBackupCode, verifyTotpForUser } from "@/lib/auth-totp";

async function seedRow(opts: { backupCodes: string[] }) {
  store.rows = [
    {
      id: "22222222-2222-2222-2222-222222222222",
      userId: USER_ID,
      // Encrypt EXACTLY as the better-auth twoFactor plugin does.
      secret: await symmetricEncrypt({ key: SECRET, data: TOTP_SECRET }),
      backupCodes: await symmetricEncrypt({
        key: SECRET,
        data: JSON.stringify(opts.backupCodes),
      }),
      verified: true,
    },
  ];
}

describe("verifyTotpForUser", () => {
  beforeEach(async () => {
    await seedRow({ backupCodes: ["aaaaa-bbbbb", "ccccc-ddddd"] });
  });

  it("returns true for a valid current TOTP code", async () => {
    const code = authenticator.generate(TOTP_SECRET);
    expect(await verifyTotpForUser(USER_ID, code)).toBe(true);
  });

  it("returns false for a wrong code", async () => {
    expect(await verifyTotpForUser(USER_ID, "000000")).toBe(false);
  });

  it("returns false when the user has no two_factors row", async () => {
    expect(
      await verifyTotpForUser("99999999-9999-9999-9999-999999999999", "000000"),
    ).toBe(false);
  });
});

describe("consumeBackupCode", () => {
  beforeEach(async () => {
    await seedRow({ backupCodes: ["aaaaa-bbbbb", "ccccc-ddddd"] });
  });

  it("accepts a valid backup code once, then rejects its reuse", async () => {
    expect(await consumeBackupCode(USER_ID, "aaaaa-bbbbb")).toBe(true);
    // Consumed: a second use of the same code must fail.
    expect(await consumeBackupCode(USER_ID, "aaaaa-bbbbb")).toBe(false);
  });

  it("leaves the OTHER backup codes usable after consuming one", async () => {
    expect(await consumeBackupCode(USER_ID, "aaaaa-bbbbb")).toBe(true);
    // The untouched code still verifies.
    expect(await consumeBackupCode(USER_ID, "ccccc-ddddd")).toBe(true);
  });

  it("returns false for an unknown backup code", async () => {
    expect(await consumeBackupCode(USER_ID, "zzzzz-zzzzz")).toBe(false);
  });

  it("returns false when the user has no two_factors row", async () => {
    expect(
      await consumeBackupCode(
        "99999999-9999-9999-9999-999999999999",
        "aaaaa-bbbbb",
      ),
    ).toBe(false);
  });
});
