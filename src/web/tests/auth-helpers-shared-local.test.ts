import { describe, expect, test, vi, beforeEach } from "vitest";

// Drive getUserId's session lookup via a mutable holder (getUserId calls
// auth.api.getSession). Mock persist for LOCAL_USER_ID + a DB-free
// ensureLocalUser. Mock the drizzle db with a controllable users list so we
// can exercise the owner-resolution (pre-login / single-owner / multi-user)
// without a real Postgres. bridge/auth's isSharedLocalToken runs REAL (it only
// needs the env var + a constant-time compare).
let session: unknown = null;
let realUsers: Array<{ id: string }> = [];

vi.mock("@/lib/auth", () => ({
  auth: { api: { getSession: async () => session } },
}));
vi.mock("@/lib/chat/persist", () => ({
  LOCAL_USER_ID: "LOCAL-CANONICAL-ID",
  ensureLocalUser: async () => {},
}));
vi.mock("@/lib/db", () => {
  // Chainable stub: select().from().where().limit() → realUsers.
  const chain = {
    select: () => chain,
    from: () => chain,
    where: () => chain,
    limit: async () => realUsers,
  };
  return { db: chain, schema: { users: { id: "id" } } };
});

import { requireUserIdOrSharedLocal, Unauthenticated } from "@/lib/auth-helpers";

const SHARED = "shared-local-token-value";

function hdr(auth?: string): Headers {
  const h = new Headers();
  if (auth) h.set("authorization", auth);
  return h;
}

beforeEach(() => {
  session = null;
  realUsers = [];
  process.env.JARVIS_LOCAL_API_TOKEN = SHARED;
});

describe("requireUserIdOrSharedLocal", () => {
  test("valid session cookie → that user id (bearer irrelevant)", async () => {
    session = { user: { id: "u-42" }, session: { createdAt: new Date() } };
    expect(await requireUserIdOrSharedLocal(hdr("Bearer " + SHARED))).toBe("u-42");
  });

  test("shared bearer, one real user → that real owner (not LOCAL)", async () => {
    realUsers = [{ id: "real-owner-99" }];
    expect(await requireUserIdOrSharedLocal(hdr("Bearer " + SHARED))).toBe(
      "real-owner-99",
    );
  });

  test("shared bearer, no real user yet (pre-login) → LOCAL_USER_ID", async () => {
    realUsers = [];
    expect(await requireUserIdOrSharedLocal(hdr("Bearer " + SHARED))).toBe(
      "LOCAL-CANONICAL-ID",
    );
  });

  test("shared bearer, multi-user box (ambiguous) → Unauthenticated", async () => {
    realUsers = [{ id: "a" }, { id: "b" }];
    await expect(
      requireUserIdOrSharedLocal(hdr("Bearer " + SHARED)),
    ).rejects.toBeInstanceOf(Unauthenticated);
  });

  test("no session + a WRONG bearer → Unauthenticated (not a fall-through)", async () => {
    await expect(
      requireUserIdOrSharedLocal(hdr("Bearer not-the-token")),
    ).rejects.toBeInstanceOf(Unauthenticated);
  });

  test("no session + no bearer → Unauthenticated", async () => {
    await expect(requireUserIdOrSharedLocal(hdr())).rejects.toBeInstanceOf(
      Unauthenticated,
    );
  });

  test("shared bearer rejected when JARVIS_LOCAL_API_TOKEN is unset (fail-closed)", async () => {
    delete process.env.JARVIS_LOCAL_API_TOKEN;
    realUsers = [{ id: "real-owner-99" }];
    await expect(
      requireUserIdOrSharedLocal(hdr("Bearer " + SHARED)),
    ).rejects.toBeInstanceOf(Unauthenticated);
  });
});
