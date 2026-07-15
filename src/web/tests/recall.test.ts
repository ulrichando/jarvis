// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { signProxyToken } from "@/lib/bridge/proxyJwt";

// Integration tests for /api/recall — honcho semantic recall for the voice
// agent (Phase 2 of cloud voice memory). Postgres is replaced with an
// in-memory users table (resolveServiceUserId is the only DB consumer);
// proxy-JWT verification runs REAL (same harness as voice-memory.test.ts /
// curated-memory.test.ts). Honcho's v3 REST is a fetch mock. The point: the
// service-token auth contract (sub === "voice-agent" mandatory), the exact
// honcho v3 wire shapes (ensure → dialectic chat / batch-wrapped
// add_messages), and the fail-soft contract when honcho isn't deployed.

const SECRET = "recall-test-secret-0123456789";
process.env.JARVIS_PROXY_JWT_SECRET = SECRET;

const USER_A = "11111111-1111-4111-8111-111111111111";
const HONCHO = "http://honcho.internal:8000";

// ── In-memory users table (resolveServiceUserId) ───────────────────────────
type Row = Record<string, unknown>;
const tables: Record<string, Row[]> = { users: [] };

type Pred = (row: Row) => boolean;

vi.mock("drizzle-orm", async (orig) => {
  const actual = await orig<typeof import("drizzle-orm")>();
  return {
    ...actual,
    eq:
      (col: { __col: string }, value: unknown): Pred =>
      (row) =>
        row[col.__col] === value,
  };
});

vi.mock("@/lib/db", () => {
  const schema = new Proxy(
    {},
    {
      get: (_t, table: string) =>
        new Proxy(
          { __table: table },
          {
            get: (_base: Row, col: string) =>
              col === "__table" ? table : { __table: table, __col: col },
          },
        ),
    },
  );

  const db = {
    select: (_cols?: unknown) => ({
      from: (t: { __table: string }) => {
        let pred: Pred = () => true;
        const chain = {
          where(p: Pred) {
            pred = p;
            return chain;
          },
          async limit(n: number) {
            return tables[t.__table]!.filter(pred)
              .slice(0, n)
              .map((r) => ({ ...r }));
          },
        };
        return chain;
      },
    }),
  };

  return { db, schema, persistenceEnabled: true };
});

// Session-cookie auth, controllable per-test (default: no session).
let sessionUserId: string | null = null;
vi.mock("@/lib/auth-helpers", () => ({
  getUserId: async () => sessionUserId,
}));

// ── Honcho fetch mock ───────────────────────────────────────────────────────
function jsonRes(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

let fetchMock: ReturnType<typeof vi.fn>;

/** Default honcho: ensures 200, chat 200 with prose, messages 201. */
function honchoHappy(chatContent = "recalled prose") {
  fetchMock.mockImplementation(async (input: unknown) => {
    const url = String(input);
    if (url.endsWith("/chat")) return jsonRes(200, { content: chatContent });
    if (url.endsWith("/messages")) return jsonRes(201, [{ id: "m1" }]);
    return jsonRes(200, { id: "ensured" });
  });
}

function calls(): Array<{ url: string; body: Row; headers: Row }> {
  return fetchMock.mock.calls.map((c) => {
    const init = (c[1] ?? {}) as RequestInit;
    return {
      url: String(c[0]),
      body: init.body ? (JSON.parse(String(init.body)) as Row) : {},
      headers: (init.headers ?? {}) as Row,
    };
  });
}

// ── Helpers ─────────────────────────────────────────────────────────────────
const serviceToken = () =>
  signProxyToken({ sub: "voice-agent", ttlSeconds: 3600 }, SECRET);
const userSubToken = () =>
  signProxyToken({ sub: USER_A, ttlSeconds: 3600 }, SECRET);

function post(body: unknown, token?: string): Request {
  const headers: Record<string, string> = {
    "content-type": "application/json",
  };
  if (token) headers.authorization = `Bearer ${token}`;
  return new Request("http://127.0.0.1:3000/api/recall", {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
}

// Fresh module per test: the route caches honcho ensure results at module
// level, and each test needs a deterministic ensure sequence.
async function route() {
  return import("@/app/api/recall/route");
}

beforeEach(() => {
  vi.resetModules();
  tables.users = [{ id: USER_A }];
  sessionUserId = null;
  process.env.JARVIS_PROXY_JWT_SECRET = SECRET;
  process.env.HONCHO_BASE_URL = HONCHO;
  process.env.HONCHO_API_KEY = "test-key";
  fetchMock = vi.fn();
  honchoHappy();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ── Auth gating (same contract as /api/voice-memory) ───────────────────────
describe("auth gating", () => {
  it("a valid proxy-JWT whose sub is a userId (not voice-agent) is 403", async () => {
    const { POST } = await route();
    const tok = userSubToken();
    expect(
      (await POST(post({ mode: "query", user_id: USER_A, query: "q" }, tok)))
        .status,
    ).toBe(403);
    expect(
      (
        await POST(
          post({ mode: "sync", user_id: USER_A, role: "user", text: "t" }, tok),
        )
      ).status,
    ).toBe(403);
    // Honcho never contacted on rejected requests.
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("no auth at all is 401; a garbage bearer is 401", async () => {
    const { POST } = await route();
    expect(
      (await POST(post({ mode: "query", user_id: USER_A, query: "q" }))).status,
    ).toBe(401);
    expect(
      (
        await POST(
          post({ mode: "query", user_id: USER_A, query: "q" }, "not.a.jwt"),
        )
      ).status,
    ).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects a malformed user_id with 400 and an unknown one with 404", async () => {
    const { POST } = await route();
    const tok = serviceToken();
    expect(
      (await POST(post({ mode: "query", user_id: "not-a-uuid", query: "q" }, tok)))
        .status,
    ).toBe(400);
    expect(
      (
        await POST(
          post(
            {
              mode: "sync",
              user_id: "99999999-9999-4999-8999-999999999999",
              role: "user",
              text: "t",
            },
            tok,
          ),
        )
      ).status,
    ).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("a better-auth session works without user_id (session user wins)", async () => {
    sessionUserId = USER_A;
    const { POST } = await route();
    const res = await POST(post({ mode: "query", query: "what do I run?" }));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ text: "recalled prose" });
    // Keyed by the SESSION user's peer.
    const chat = calls().find((c) => c.url.endsWith("/chat"))!;
    expect(chat.url).toBe(
      `${HONCHO}/v3/workspaces/default/peers/user-${USER_A}/chat`,
    );
  });
});

// ── Body validation ─────────────────────────────────────────────────────────
describe("validation", () => {
  it("rejects a missing/unknown mode and invalid JSON with 400", async () => {
    const { POST } = await route();
    const tok = serviceToken();
    expect(
      (await POST(post({ user_id: USER_A, query: "q" }, tok))).status,
    ).toBe(400);
    expect(
      (await POST(post({ mode: "zap", user_id: USER_A }, tok))).status,
    ).toBe(400);
    const bad = new Request("http://127.0.0.1:3000/api/recall", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${tok}`,
      },
      body: "{not json",
    });
    expect((await POST(bad)).status).toBe(400);
  });

  it("query mode requires a non-empty query", async () => {
    const { POST } = await route();
    const res = await POST(
      post({ mode: "query", user_id: USER_A, query: "  " }, serviceToken()),
    );
    expect(res.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sync mode requires role ∈ user|assistant and non-empty text", async () => {
    const { POST } = await route();
    const tok = serviceToken();
    expect(
      (
        await POST(
          post({ mode: "sync", user_id: USER_A, role: "tool", text: "t" }, tok),
        )
      ).status,
    ).toBe(400);
    expect(
      (
        await POST(
          post({ mode: "sync", user_id: USER_A, role: "user", text: "  " }, tok),
        )
      ).status,
    ).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

// ── Query (dialectic chat) ──────────────────────────────────────────────────
describe("mode=query — honcho dialectic recall", () => {
  it("ensures workspace + user peer, then chats the peer with the v3 shape", async () => {
    const { POST } = await route();
    const res = await POST(
      post(
        { mode: "query", user_id: USER_A, query: "where does Ulrich work?" },
        serviceToken(),
      ),
    );
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ text: "recalled prose" });

    const seq = calls();
    expect(seq.map((c) => c.url)).toEqual([
      `${HONCHO}/v3/workspaces`,
      `${HONCHO}/v3/workspaces/default/peers`,
      `${HONCHO}/v3/workspaces/default/peers/user-${USER_A}/chat`,
    ]);
    expect(seq[0]!.body).toEqual({ id: "default" });
    expect(seq[1]!.body).toEqual({ id: `user-${USER_A}` });
    expect(seq[2]!.body).toEqual({
      query: "where does Ulrich work?",
      stream: false,
      reasoning_level: "low",
    });
    // Bearer sent even though AUTH_USE_AUTH=false servers ignore it.
    expect(seq[2]!.headers.authorization).toBe("Bearer test-key");
  });

  it("caps the recall text at 8000 chars", async () => {
    honchoHappy("x".repeat(9000));
    const { POST } = await route();
    const res = await POST(
      post({ mode: "query", user_id: USER_A, query: "q" }, serviceToken()),
    );
    const { text } = (await res.json()) as { text: string };
    expect(text).toHaveLength(8000);
  });

  it("a null dialectic content is an empty recall, not an error", async () => {
    fetchMock.mockImplementation(async (input: unknown) =>
      String(input).endsWith("/chat")
        ? jsonRes(200, { content: null })
        : jsonRes(200, { id: "ensured" }),
    );
    const { POST } = await route();
    const res = await POST(
      post({ mode: "query", user_id: USER_A, query: "q" }, serviceToken()),
    );
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ text: "" });
  });
});

// ── Sync (add_messages) ─────────────────────────────────────────────────────
describe("mode=sync — honcho turn ingestion", () => {
  it("ensures workspace/peers/session then POSTs the batch-wrapped message", async () => {
    const { POST } = await route();
    const res = await POST(
      post(
        {
          mode: "sync",
          user_id: USER_A,
          role: "user",
          text: "hello there",
          session_id: "voice room 42",
        },
        serviceToken(),
      ),
    );
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });

    const sid = `web-${USER_A}-voice-room-42`; // sanitized + user-namespaced
    const seq = calls();
    expect(seq.map((c) => c.url)).toEqual([
      `${HONCHO}/v3/workspaces`,
      `${HONCHO}/v3/workspaces/default/peers`,
      `${HONCHO}/v3/workspaces/default/peers`,
      `${HONCHO}/v3/workspaces/default/sessions`,
      `${HONCHO}/v3/workspaces/default/sessions/${sid}/messages`,
    ]);
    expect(seq[1]!.body).toEqual({ id: `user-${USER_A}` });
    expect(seq[2]!.body).toEqual({ id: "jarvis" });
    expect(seq[3]!.body).toEqual({ id: sid });
    // MessageBatchCreate: batch-wrapped, peer-attributed.
    expect(seq[4]!.body).toEqual({
      messages: [{ content: "hello there", peer_id: `user-${USER_A}` }],
    });
  });

  it("assistant turns are attributed to the shared jarvis peer; session_id defaults", async () => {
    const { POST } = await route();
    const res = await POST(
      post(
        { mode: "sync", user_id: USER_A, role: "assistant", text: "hi" },
        serviceToken(),
      ),
    );
    expect(res.status).toBe(200);
    const msg = calls().find((c) => c.url.endsWith("/messages"))!;
    expect(msg.url).toBe(
      `${HONCHO}/v3/workspaces/default/sessions/web-${USER_A}-voice/messages`,
    );
    expect(msg.body).toEqual({
      messages: [{ content: "hi", peer_id: "jarvis" }],
    });
  });

  it("caches ensure calls: a second sync in the same process is ONE honcho call", async () => {
    const { POST } = await route();
    const body = {
      mode: "sync",
      user_id: USER_A,
      role: "user",
      session_id: "room-1",
    };
    await POST(post({ ...body, text: "one" }, serviceToken()));
    const afterFirst = fetchMock.mock.calls.length; // 4 ensures + 1 messages
    expect(afterFirst).toBe(5);
    await POST(post({ ...body, text: "two" }, serviceToken()));
    expect(fetchMock.mock.calls.length).toBe(afterFirst + 1);
    expect(calls().at(-1)!.url).toContain("/messages");
  });
});

// ── Fail-soft contract ──────────────────────────────────────────────────────
describe("fail soft — honcho unset or unreachable", () => {
  it("HONCHO_BASE_URL unset: query → {text:''}, sync → no-op; honcho never called", async () => {
    delete process.env.HONCHO_BASE_URL;
    const { POST } = await route();
    const tok = serviceToken();

    const q = await POST(post({ mode: "query", user_id: USER_A, query: "q" }, tok));
    expect(q.status).toBe(200);
    expect(await q.json()).toEqual({ text: "" });

    const s = await POST(
      post({ mode: "sync", user_id: USER_A, role: "user", text: "t" }, tok),
    );
    expect(s.status).toBe(200);
    expect(await s.json()).toEqual({ ok: true, skipped: true });

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("honcho unreachable (fetch rejects): query fails soft with empty text", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    fetchMock.mockRejectedValue(new Error("ECONNREFUSED"));
    const { POST } = await route();
    const res = await POST(
      post({ mode: "query", user_id: USER_A, query: "q" }, serviceToken()),
    );
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({
      text: "",
      error: "recall backend unavailable",
    });
  });

  it("honcho 5xx on chat: query fails soft; sync reports ok:false without throwing", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    fetchMock.mockImplementation(async (input: unknown) => {
      const url = String(input);
      if (url.endsWith("/chat") || url.endsWith("/messages")) {
        return jsonRes(500, { detail: "boom" });
      }
      return jsonRes(200, { id: "ensured" });
    });
    const { POST } = await route();
    const tok = serviceToken();

    const q = await POST(post({ mode: "query", user_id: USER_A, query: "q" }, tok));
    expect(q.status).toBe(200);
    expect(((await q.json()) as { text: string }).text).toBe("");

    const s = await POST(
      post({ mode: "sync", user_id: USER_A, role: "user", text: "t" }, tok),
    );
    expect(s.status).toBe(200);
    expect(((await s.json()) as { ok: boolean }).ok).toBe(false);
  });
});
