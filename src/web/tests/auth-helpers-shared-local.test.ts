import { describe, expect, test, vi, beforeEach } from "vitest";

// Drive getUserId's session lookup via a mutable holder (getUserId calls
// auth.api.getSession). Mock persist so the LOCAL_USER_ID resolution doesn't
// touch the DB. bridge/auth's isSharedLocalToken is used REAL — it only needs
// the env var + a constant-time compare.
let session: unknown = null;
vi.mock("@/lib/auth", () => ({
  auth: { api: { getSession: async () => session } },
}));
vi.mock("@/lib/chat/persist", () => ({
  LOCAL_USER_ID: "LOCAL-CANONICAL-ID",
  ensureLocalUser: async () => {},
}));

import { requireUserIdOrSharedLocal, Unauthenticated } from "@/lib/auth-helpers";

const SHARED = "shared-local-token-value";

function hdr(auth?: string): Headers {
  const h = new Headers();
  if (auth) h.set("authorization", auth);
  return h;
}

beforeEach(() => {
  session = null;
  process.env.JARVIS_LOCAL_API_TOKEN = SHARED;
});

describe("requireUserIdOrSharedLocal", () => {
  test("valid session cookie → that user id (bearer irrelevant)", async () => {
    session = { user: { id: "u-42" }, session: { createdAt: new Date() } };
    expect(await requireUserIdOrSharedLocal(hdr("Bearer " + SHARED))).toBe("u-42");
  });

  test("no session + the shared bearer → the canonical single-user id", async () => {
    expect(await requireUserIdOrSharedLocal(hdr("Bearer " + SHARED))).toBe(
      "LOCAL-CANONICAL-ID",
    );
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
    await expect(
      requireUserIdOrSharedLocal(hdr("Bearer " + SHARED)),
    ).rejects.toBeInstanceOf(Unauthenticated);
  });
});
