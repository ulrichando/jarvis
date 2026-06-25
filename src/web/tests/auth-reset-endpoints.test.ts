// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";

// Integration tests for the three TOTP-authorized password-reset endpoints.
// Postgres is replaced with an in-memory store; @/lib/auth-totp is faked so we
// control the verify outcome (its real crypto is covered in auth-totp.test.ts).
// The point here is the ENDPOINT behaviour + the threat model: no enumeration,
// rate limiting, single-use 10-min token, sessions revoked.

const SECRET = "test-better-auth-secret-0123456789abcdef";
process.env.BETTER_AUTH_SECRET = SECRET;

// ── In-memory tables ────────────────────────────────────────────────────────
type Row = Record<string, unknown>;
const tables: Record<string, Row[]> = {
  users: [],
  accounts: [],
  sessions: [],
  passwordResets: [],
};
// Map a drizzle "table" token → our table name. The schema proxy tags columns
// with their owning table so predicates + writes resolve to the right array.
function tableNameOf(t: { __table: string }): string {
  return t.__table;
}

// Predicate model: drizzle operators (eq/and/isNull/gt) compile to (row)=>bool.
type Pred = (row: Row) => boolean;

vi.mock("drizzle-orm", async (orig) => {
  const actual = await orig<typeof import("drizzle-orm")>();
  return {
    ...actual,
    eq:
      (col: { __col: string }, value: unknown): Pred =>
      (row) =>
        row[col.__col] === value,
    isNull:
      (col: { __col: string }): Pred =>
      (row) =>
        row[col.__col] == null,
    gt:
      (col: { __col: string }, value: unknown): Pred =>
      (row) =>
        (row[col.__col] as number | Date) > (value as number | Date),
    and:
      (...preds: Pred[]): Pred =>
      (row) =>
        preds.every((p) => p(row)),
  };
});

vi.mock("@/lib/db", () => {
  // schema.<table>.<col> → { __table, __col }
  const schema = new Proxy(
    {},
    {
      get: (_t, table: string) =>
        new Proxy(
          { __table: table },
          { get: (base: Row, col: string) => (col === "__table" ? table : { __table: table, __col: col }) },
        ),
    },
  ) as Record<string, Record<string, { __table: string; __col: string }>>;

  const db = {
    select: (_cols?: unknown) => ({
      from: (t: { __table: string }) => {
        const name = tableNameOf(t);
        let pred: Pred = () => true;
        const chain = {
          where: (p: Pred) => {
            pred = p;
            return chain;
          },
          limit: async (_n: number) => tables[name]!.filter(pred),
        };
        return chain;
      },
    }),
    insert: (t: { __table: string }) => ({
      values: async (vals: Row) => {
        const row = { id: `id-${Math.random().toString(36).slice(2)}`, ...vals };
        tables[tableNameOf(t)]!.push(row);
        return [row];
      },
    }),
    update: (t: { __table: string }) => ({
      set: (patch: Row) => {
        const name = tableNameOf(t);
        return {
          where: (pred: Pred) => {
            const hit = tables[name]!.filter(pred);
            for (const r of hit) Object.assign(r, patch);
            return {
              returning: async (_c?: unknown) => hit.map((r) => ({ id: r.id })),
            };
          },
        };
      },
    }),
    delete: (t: { __table: string }) => ({
      where: async (pred: Pred) => {
        const name = tableNameOf(t);
        tables[name] = tables[name]!.filter((r) => !pred(r));
      },
    }),
  };

  return { db, schema };
});

// Control verify outcomes per-test. Defined via vi.hoisted so they exist when
// the (hoisted) vi.mock factory runs.
const { verifyTotpForUser, consumeBackupCode } = vi.hoisted(() => ({
  verifyTotpForUser: vi.fn(async (_u: string, _c: string) => false),
  consumeBackupCode: vi.fn(async (_u: string, _c: string) => false),
}));
vi.mock("@/lib/auth-totp", () => ({ verifyTotpForUser, consumeBackupCode }));

import { __resetRateLimitState } from "@/lib/reset-ratelimit";
import { POST as requestPost } from "@/app/api/auth/reset/request/route";
import { POST as verifyPost } from "@/app/api/auth/reset/verify/route";
import { POST as completePost } from "@/app/api/auth/reset/complete/route";

const USER_ID = "user-1";
function jsonReq(body: unknown, headers: Record<string, string> = {}): Request {
  return new Request("http://127.0.0.1:3000/api/auth/reset/x", {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
}

beforeEach(() => {
  tables.users = [{ id: USER_ID, email: "owner@example.com" }];
  tables.accounts = [
    { id: "acc-1", userId: USER_ID, providerId: "credential", password: "OLDHASH" },
  ];
  tables.sessions = [
    { id: "sess-1", userId: USER_ID, token: "t1" },
    { id: "sess-2", userId: USER_ID, token: "t2" },
  ];
  tables.passwordResets = [];
  verifyTotpForUser.mockReset().mockResolvedValue(false);
  consumeBackupCode.mockReset().mockResolvedValue(false);
  __resetRateLimitState();
});

describe("POST /reset/request", () => {
  it("returns { ok: true } for a known email", async () => {
    const res = await requestPost(jsonReq({ email: "owner@example.com" }));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });
  });

  it("returns the SAME { ok: true } for an unknown email (no enumeration)", async () => {
    const res = await requestPost(jsonReq({ email: "nobody@example.com" }));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });
  });

  it("returns { ok: true } even for a missing/garbage body", async () => {
    const res = await requestPost(jsonReq({}));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });
  });
});

describe("POST /reset/verify", () => {
  it("mints a token on a valid TOTP code", async () => {
    verifyTotpForUser.mockResolvedValue(true);
    const res = await verifyPost(jsonReq({ email: "owner@example.com", code: "123456" }));
    expect(res.status).toBe(200);
    const j = (await res.json()) as { token: string };
    expect(typeof j.token).toBe("string");
    expect(j.token.length).toBeGreaterThan(10);
    // Row persisted with a ~10-minute expiry, unused.
    expect(tables.passwordResets).toHaveLength(1);
    const row = tables.passwordResets[0]!;
    expect(row.userId).toBe(USER_ID);
    expect(row.usedAt ?? null).toBeNull();
    const ms = (row.expiresAt as Date).getTime() - Date.now();
    expect(ms).toBeGreaterThan(9 * 60 * 1000);
    expect(ms).toBeLessThanOrEqual(10 * 60 * 1000 + 1000);
  });

  it("mints a token on a valid backup code", async () => {
    consumeBackupCode.mockResolvedValue(true);
    const res = await verifyPost(jsonReq({ email: "owner@example.com", code: "aaaaa-bbbbb" }));
    expect(res.status).toBe(200);
    expect(tables.passwordResets).toHaveLength(1);
  });

  it("401 generic on a wrong code, no token row", async () => {
    const res = await verifyPost(jsonReq({ email: "owner@example.com", code: "000000" }));
    expect(res.status).toBe(401);
    expect(tables.passwordResets).toHaveLength(0);
  });

  it("unknown email returns the SAME 401 shape as a wrong code (no enumeration)", async () => {
    // Both verify paths fail (default mock = false). A real user with a wrong
    // code and an unknown email must be indistinguishable to the caller: same
    // status, same body. (Different IPs so the rate-limiter doesn't interfere.)
    const wrong = await verifyPost(
      jsonReq({ email: "owner@example.com", code: "000000" }, { "x-forwarded-for": "3.3.3.3" }),
    );
    const unknown = await verifyPost(
      jsonReq({ email: "ghost@example.com", code: "000000" }, { "x-forwarded-for": "4.4.4.4" }),
    );
    expect(unknown.status).toBe(401);
    expect(wrong.status).toBe(401);
    expect(await unknown.json()).toEqual(await wrong.json());
  });

  it("rate-limits after 5 attempts per (email+IP) → 429", async () => {
    const ip = { "x-forwarded-for": "9.9.9.9" };
    for (let i = 0; i < 5; i++) {
      const r = await verifyPost(jsonReq({ email: "owner@example.com", code: "000000" }, ip));
      expect(r.status).toBe(401); // wrong code, but within budget
    }
    const sixth = await verifyPost(jsonReq({ email: "owner@example.com", code: "000000" }, ip));
    expect(sixth.status).toBe(429);
  });

  it("rate-limit is per-IP: a different IP is NOT throttled", async () => {
    for (let i = 0; i < 6; i++) {
      await verifyPost(jsonReq({ email: "owner@example.com", code: "000000" }, { "x-forwarded-for": "1.1.1.1" }));
    }
    verifyTotpForUser.mockResolvedValue(true);
    const other = await verifyPost(jsonReq({ email: "owner@example.com", code: "123456" }, { "x-forwarded-for": "2.2.2.2" }));
    expect(other.status).toBe(200);
  });
});

describe("POST /reset/complete", () => {
  async function mintToken(): Promise<string> {
    verifyTotpForUser.mockResolvedValue(true);
    const res = await verifyPost(jsonReq({ email: "owner@example.com", code: "123456" }));
    verifyTotpForUser.mockResolvedValue(false);
    return ((await res.json()) as { token: string }).token;
  }

  it("sets a new password hash, marks token used, revokes sessions", async () => {
    const token = await mintToken();
    const res = await completePost(jsonReq({ token, password: "newpassword1" }));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });

    // Credential password replaced with a scrypt "salt:hex" (not the old value).
    const acc = tables.accounts.find((a) => a.providerId === "credential")!;
    expect(acc.password).not.toBe("OLDHASH");
    expect(String(acc.password)).toMatch(/^[0-9a-f]+:[0-9a-f]+$/);

    // Token consumed.
    expect(tables.passwordResets[0]!.usedAt).toBeInstanceOf(Date);

    // All of the user's sessions are gone.
    expect(tables.sessions.filter((s) => s.userId === USER_ID)).toHaveLength(0);
  });

  it("rejects a reused (already-used) token", async () => {
    const token = await mintToken();
    expect((await completePost(jsonReq({ token, password: "newpassword1" }))).status).toBe(200);
    const second = await completePost(jsonReq({ token, password: "anotherpass9" }));
    expect(second.status).toBe(400);
  });

  it("rejects an unknown token", async () => {
    const res = await completePost(jsonReq({ token: "does-not-exist", password: "newpassword1" }));
    expect(res.status).toBe(400);
  });

  it("rejects an expired token", async () => {
    // Seed an already-expired token directly.
    tables.passwordResets = [
      {
        id: "pr-exp",
        userId: USER_ID,
        token: "expired-token",
        expiresAt: new Date(Date.now() - 1000),
        usedAt: null,
      },
    ];
    const res = await completePost(jsonReq({ token: "expired-token", password: "newpassword1" }));
    expect(res.status).toBe(400);
    // Password untouched.
    expect(tables.accounts.find((a) => a.providerId === "credential")!.password).toBe("OLDHASH");
  });

  it("rejects a too-short password (>= 8 enforced)", async () => {
    const token = await mintToken();
    const res = await completePost(jsonReq({ token, password: "short" }));
    expect(res.status).toBe(400);
    // Token NOT consumed on a validation failure (length checked before claim).
    expect(tables.passwordResets[0]!.usedAt ?? null).toBeNull();
  });

  it("creates a credential account if the user had none", async () => {
    tables.accounts = []; // e.g. OAuth-only user adding a password
    const token = await mintToken();
    const res = await completePost(jsonReq({ token, password: "newpassword1" }));
    expect(res.status).toBe(200);
    const acc = tables.accounts.find((a) => a.providerId === "credential");
    expect(acc).toBeTruthy();
    expect(String(acc!.password)).toMatch(/^[0-9a-f]+:[0-9a-f]+$/);
  });
});
