// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";

import { signProxyToken } from "@/lib/bridge/proxyJwt";

// Integration tests for /api/voice-memory — the voice agent's cloud
// conversation memory. Postgres is replaced with an in-memory store (same
// pattern as auth-reset-endpoints.test.ts); the proxy-JWT verification runs
// REAL (signProxyToken/verifyProxyToken + the env-var secret path of
// getOrCreateProxyJwtSecret). The point: the service-token auth contract
// (sub === "voice-agent" mandatory), find-or-create of the single voice
// conversation, and chronological turn readback.

const SECRET = "voice-memory-test-secret-0123456789";
process.env.JARVIS_PROXY_JWT_SECRET = SECRET;

const USER_A = "11111111-1111-4111-8111-111111111111";

// ── In-memory tables ────────────────────────────────────────────────────────
type Row = Record<string, unknown>;
const tables: Record<string, Row[]> = {
  users: [],
  conversations: [],
  messages: [],
};
let idSeq = 0;
let clock = 0;

function tableNameOf(t: { __table: string }): string {
  return t.__table;
}

type Pred = (row: Row) => boolean;

vi.mock("drizzle-orm", async (orig) => {
  const actual = await orig<typeof import("drizzle-orm")>();
  return {
    ...actual,
    eq:
      (col: { __col: string }, value: unknown): Pred =>
      (row) =>
        row[col.__col] === value,
    and:
      (...preds: Pred[]): Pred =>
      (row) =>
        preds.every((p) => p(row)),
    inArray:
      (col: { __col: string }, values: unknown[]): Pred =>
      (row) =>
        values.includes(row[col.__col]),
    // The route only orders by created_at — tagged tokens are enough.
    desc: (col: { __col: string }) => ({ __col: col.__col, __dir: "desc" }),
    asc: (col: { __col: string }) => ({ __col: col.__col, __dir: "asc" }),
  };
});

vi.mock("@/lib/db", () => {
  // schema.<table>.<col> → { __table, __col } (camelCase col keys, matching
  // how the route addresses them — the mock stores rows under the same keys).
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
        const name = tableNameOf(t);
        let pred: Pred = () => true;
        let order: { __col: string; __dir?: string } | null = null;
        const chain = {
          where(p: Pred) {
            pred = p;
            return chain;
          },
          orderBy(o: { __col: string; __dir?: string }) {
            order = o;
            return chain;
          },
          async limit(n: number) {
            let rows = tables[name]!.filter(pred);
            if (order) {
              const first = order.__dir === "asc" ? -1 : 1;
              rows = [...rows].sort((a, b) =>
                (a[order!.__col] as number) < (b[order!.__col] as number)
                  ? first
                  : -first,
              );
            }
            return rows.slice(0, n).map((r) => ({ ...r }));
          },
        };
        return chain;
      },
    }),
    insert: (t: { __table: string }) => ({
      values: (vals: Row) => {
        const name = tableNameOf(t);
        let skipOnConflict = false;
        // Deferred write so onConflictDoNothing can enforce the partial
        // unique index (conversations_voice_user_uniq: one kind='voice'
        // conversation per user) the way real Postgres would.
        const run = (): Row[] => {
          if (
            skipOnConflict &&
            name === "conversations" &&
            vals.kind === "voice" &&
            tables.conversations!.some(
              (r) => r.userId === vals.userId && r.kind === "voice",
            )
          ) {
            return [];
          }
          const row: Row = { id: `${name}-${++idSeq}`, createdAt: ++clock, ...vals };
          tables[name]!.push(row);
          return [row];
        };
        const chain = {
          onConflictDoNothing() {
            skipOnConflict = true;
            return chain;
          },
          returning: async (_c?: unknown) => run(),
          then<T>(resolve: (rows: Row[]) => T, reject?: (err: unknown) => T) {
            return Promise.resolve().then(run).then(resolve, reject);
          },
        };
        return chain;
      },
    }),
    update: (t: { __table: string }) => ({
      set: (patch: Row) => ({
        where: async (pred: Pred) => {
          for (const r of tables[tableNameOf(t)]!) {
            if (pred(r)) Object.assign(r, patch);
          }
        },
      }),
    }),
  };

  return { db, schema, persistenceEnabled: true };
});

vi.mock("@/lib/db/ensure-schema", () => ({
  ensureWebSchema: async () => {},
}));

// Session-cookie auth, controllable per-test (default: no session).
let sessionUserId: string | null = null;
vi.mock("@/lib/auth-helpers", () => ({
  getUserId: async () => sessionUserId,
}));

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
  return new Request("http://127.0.0.1:3000/api/voice-memory", {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
}

function get(qs: string, token?: string): Request {
  const headers: Record<string, string> = {};
  if (token) headers.authorization = `Bearer ${token}`;
  return new Request(`http://127.0.0.1:3000/api/voice-memory${qs}`, {
    headers,
  });
}

async function route() {
  return import("@/app/api/voice-memory/route");
}

beforeEach(() => {
  tables.users = [{ id: USER_A }];
  tables.conversations = [];
  tables.messages = [];
  sessionUserId = null;
  process.env.JARVIS_PROXY_JWT_SECRET = SECRET;
});

// ── Tests ───────────────────────────────────────────────────────────────────
describe("POST+GET /api/voice-memory — service-token roundtrip", () => {
  it("voice-agent token POSTs a turn then GETs it back for the given user_id", async () => {
    const { GET, POST } = await route();
    const tok = serviceToken();

    const postRes = await POST(
      post({ role: "user", text: "hello there", user_id: USER_A }, tok),
    );
    expect(postRes.status).toBe(200);
    const { conversationId } = (await postRes.json()) as {
      conversationId: string;
    };
    expect(conversationId).toBeTruthy();

    const getRes = await GET(get(`?user_id=${USER_A}&limit=40`, tok));
    expect(getRes.status).toBe(200);
    const { turns } = (await getRes.json()) as {
      turns: Array<{ role: string; text: string }>;
    };
    expect(turns).toEqual([{ role: "user", text: "hello there" }]);
  });

  it("rejects a malformed user_id with 400 and an unknown one with 404 (never 500)", async () => {
    const { GET, POST } = await route();
    const tok = serviceToken();
    expect(
      (await POST(post({ role: "user", text: "x", user_id: "not-a-uuid" }, tok)))
        .status,
    ).toBe(400);
    expect(
      (
        await POST(
          post(
            {
              role: "user",
              text: "x",
              user_id: "99999999-9999-4999-8999-999999999999",
            },
            tok,
          ),
        )
      ).status,
    ).toBe(404);
    expect((await GET(get("?user_id=not-a-uuid", tok))).status).toBe(400);
    expect(
      (await GET(get("?user_id=99999999-9999-4999-8999-999999999999", tok)))
        .status,
    ).toBe(404);
  });
});

describe("auth gating", () => {
  it("a valid proxy-JWT whose sub is a userId (not voice-agent) is 403", async () => {
    const { GET, POST } = await route();
    const tok = userSubToken();
    expect(
      (await POST(post({ role: "user", text: "hi", user_id: USER_A }, tok)))
        .status,
    ).toBe(403);
    expect((await GET(get(`?user_id=${USER_A}`, tok))).status).toBe(403);
    // Nothing was written on the rejected POST.
    expect(tables.messages).toHaveLength(0);
  });

  it("no auth at all is 401", async () => {
    const { GET, POST } = await route();
    expect(
      (await POST(post({ role: "user", text: "hi", user_id: USER_A }))).status,
    ).toBe(401);
    expect((await GET(get(`?user_id=${USER_A}`))).status).toBe(401);
  });

  it("a garbage bearer is 401 (invalid signature ≠ forbidden sub)", async () => {
    const { GET } = await route();
    expect((await GET(get(`?user_id=${USER_A}`, "not.a.jwt"))).status).toBe(401);
  });

  it("a better-auth session works without user_id (session user wins)", async () => {
    sessionUserId = USER_A;
    const { GET, POST } = await route();
    expect((await POST(post({ role: "user", text: "from web" }))).status).toBe(
      200,
    );
    const { turns } = (await (await GET(get(""))).json()) as {
      turns: Array<{ role: string; text: string }>;
    };
    expect(turns).toEqual([{ role: "user", text: "from web" }]);
  });
});

describe("single continuous voice conversation (find-or-create)", () => {
  it("repeated POSTs reuse the SAME conversation — one per user", async () => {
    const { POST } = await route();
    const tok = serviceToken();
    const first = (await (
      await POST(post({ role: "user", text: "one", user_id: USER_A }, tok))
    ).json()) as { conversationId: string };
    const second = (await (
      await POST(post({ role: "assistant", text: "two", user_id: USER_A }, tok))
    ).json()) as { conversationId: string };
    expect(second.conversationId).toBe(first.conversationId);
    expect(tables.conversations).toHaveLength(1);
    expect(tables.conversations[0]).toMatchObject({
      userId: USER_A,
      kind: "voice",
      title: "Voice conversation",
    });
    expect(tables.messages).toHaveLength(2);
  });

  it("CONCURRENT first POSTs still converge on one conversation (race)", async () => {
    const { POST } = await route();
    const tok = serviceToken();
    // Both handlers interleave on the same microtask queue, so both finds
    // resolve null before either insert lands — the loser's insert hits the
    // mock's partial-unique conflict and must re-SELECT the winner.
    const [a, b] = await Promise.all([
      POST(post({ role: "user", text: "one", user_id: USER_A }, tok)),
      POST(post({ role: "assistant", text: "two", user_id: USER_A }, tok)),
    ]);
    expect(a.status).toBe(200);
    expect(b.status).toBe(200);
    const { conversationId: idA } = (await a.json()) as { conversationId: string };
    const { conversationId: idB } = (await b.json()) as { conversationId: string };
    expect(idB).toBe(idA);
    expect(tables.conversations).toHaveLength(1);
    expect(tables.messages).toHaveLength(2);
  });
});

describe("GET readback shape", () => {
  it("returns turns chronologically with text extracted, honoring limit", async () => {
    const { GET, POST } = await route();
    const tok = serviceToken();
    for (const [role, text] of [
      ["user", "one"],
      ["assistant", "two"],
      ["user", "three"],
    ] as const) {
      const res = await POST(post({ role, text, user_id: USER_A }, tok));
      expect(res.status).toBe(200);
    }

    const all = (await (
      await GET(get(`?user_id=${USER_A}&limit=40`, tok))
    ).json()) as { turns: Array<{ role: string; text: string }> };
    expect(all.turns).toEqual([
      { role: "user", text: "one" },
      { role: "assistant", text: "two" },
      { role: "user", text: "three" },
    ]);

    // limit=2 keeps the NEWEST two, still chronological.
    const last2 = (await (
      await GET(get(`?user_id=${USER_A}&limit=2`, tok))
    ).json()) as { turns: Array<{ role: string; text: string }> };
    expect(last2.turns).toEqual([
      { role: "assistant", text: "two" },
      { role: "user", text: "three" },
    ]);
  });

  it("skips non-text/empty content and non-chat roles; no conversation → []", async () => {
    const { GET, POST } = await route();
    const tok = serviceToken();

    // No voice conversation yet → empty turns, not an error.
    const empty = (await (
      await GET(get(`?user_id=${USER_A}`, tok))
    ).json()) as { turns: unknown[] };
    expect(empty.turns).toEqual([]);

    await POST(post({ role: "user", text: "kept", user_id: USER_A }, tok));
    const convoId = tables.conversations[0]!.id;
    // Rows the web chat may write that voice history must skip: a tool
    // message, and an assistant message whose parts carry no text.
    tables.messages.push(
      {
        id: "m-tool",
        conversationId: convoId,
        role: "tool",
        content: [{ type: "text", text: "tool noise" }],
        createdAt: ++clock,
      },
      {
        id: "m-empty",
        conversationId: convoId,
        role: "assistant",
        content: [{ type: "tool-call", toolName: "x" }],
        createdAt: ++clock,
      },
    );

    const { turns } = (await (
      await GET(get(`?user_id=${USER_A}&limit=40`, tok))
    ).json()) as { turns: Array<{ role: string; text: string }> };
    expect(turns).toEqual([{ role: "user", text: "kept" }]);
  });
});
