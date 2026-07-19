// @vitest-environment node
import { beforeEach, describe, expect, it, vi } from "vitest";

// Tests for the persistent chat-memory feature:
//   - data layer round-trip (src/lib/chat/memory.ts) — add/list/remove,
//     dedup, rolling cap, per-user isolation
//   - buildMemoryBlock — formatting, char cap, empty → ""
//   - the model-callable tool (src/lib/tools/memory.ts) — actions dispatch
//     to the data layer with the factory-bound userId; invalid input comes
//     back as { status: "error" } results, never a throw.
//
// Postgres is replaced with an in-memory store (same tagged-token drizzle
// mock pattern as voice-memory.test.ts / auth-reset-endpoints.test.ts).

const USER_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const USER_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

// ── In-memory table ─────────────────────────────────────────────────────────
type Row = Record<string, unknown>;
const tables: Record<string, Row[]> = { userMemories: [] };
let idSeq = 0;
let clock = 0;

type Pred = (row: Row) => boolean;

function tableNameOf(t: { __table: string }): string {
  return t.__table;
}

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
    desc: (col: { __col: string }) => ({ __col: col.__col, __dir: "desc" }),
    asc: (col: { __col: string }) => ({ __col: col.__col, __dir: "asc" }),
  };
});

vi.mock("@/lib/db", () => {
  // schema.<table>.<col> → { __table, __col } tagged tokens (camelCase keys,
  // matching how the data layer addresses them — rows store the same keys).
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
        const chain = {
          where(p: Pred) {
            pred = p;
            return chain;
          },
          async orderBy(o: { __col: string; __dir?: string }) {
            const first = o.__dir === "asc" ? -1 : 1;
            return [...tables[name]!.filter(pred)]
              .sort((a, b) =>
                (a[o.__col] as number) < (b[o.__col] as number) ? first : -first,
              )
              .map((r) => ({ ...r }));
          },
        };
        return chain;
      },
    }),
    insert: (t: { __table: string }) => ({
      values: (vals: Row) => ({
        returning: async () => {
          const row: Row = { id: `mem-${++idSeq}`, createdAt: ++clock, ...vals };
          tables[tableNameOf(t)]!.push(row);
          return [{ ...row }];
        },
      }),
    }),
    delete: (t: { __table: string }) => ({
      where: (pred: Pred) => {
        const name = tableNameOf(t);
        const run = (): Row[] => {
          const removed = tables[name]!.filter(pred);
          tables[name] = tables[name]!.filter((r) => !pred(r));
          return removed.map((r) => ({ ...r }));
        };
        return {
          returning: async () => run(),
          then<T>(resolve: (rows: Row[]) => T, reject?: (err: unknown) => T) {
            return Promise.resolve().then(run).then(resolve, reject);
          },
        };
      },
    }),
  };

  return { db, schema, persistenceEnabled: true };
});

vi.mock("@/lib/db/ensure-schema", () => ({
  ensureWebSchema: async () => {},
}));

import {
  addMemory,
  buildMemoryBlock,
  listMemories,
  MAX_MEMORIES,
  MAX_MEMORY_CONTENT_CHARS,
  MEMORY_BLOCK_CHAR_LIMIT,
  MEMORY_BLOCK_HEADER,
  removeMemory,
} from "@/lib/chat/memory";
import { createMemoryTool } from "@/lib/tools/memory";

// tool.execute has a required second arg (ToolCallOptions) we don't use.
type ToolResult = {
  status: "ok" | "error";
  error?: string;
  memory?: { id: string; content: string };
  memories?: Array<{ id: string; content: string }>;
  count?: number;
  removedId?: string;
};
function exec(t: ReturnType<typeof createMemoryTool>, input: unknown) {
  return (
    t.execute as unknown as (i: unknown, o: unknown) => Promise<ToolResult>
  )(input, {});
}

beforeEach(() => {
  tables.userMemories = [];
  idSeq = 0;
  clock = 0;
});

// ── Data layer ──────────────────────────────────────────────────────────────

describe("chat memory data layer", () => {
  it("addMemory/listMemories round-trip, newest first, per-user isolation", async () => {
    await addMemory(USER_A, "Name is Ulrich");
    await addMemory(USER_A, "Prefers TypeScript");
    await addMemory(USER_B, "Belongs to someone else");

    const a = await listMemories(USER_A);
    expect(a.map((m) => m.content)).toEqual([
      "Prefers TypeScript",
      "Name is Ulrich",
    ]);
    expect(a.every((m) => typeof m.id === "string" && m.id.length > 0)).toBe(
      true,
    );

    const b = await listMemories(USER_B);
    expect(b.map((m) => m.content)).toEqual(["Belongs to someone else"]);
  });

  it("trims + collapses whitespace on save", async () => {
    await addMemory(USER_A, "  Works   on\n JARVIS  ");
    const [m] = await listMemories(USER_A);
    expect(m!.content).toBe("Works on JARVIS");
  });

  it("dedups case/whitespace-insensitive duplicates", async () => {
    const first = await addMemory(USER_A, "Prefers dark mode");
    const dup = await addMemory(USER_A, "  prefers   DARK mode ");
    expect(dup.id).toBe(first.id);
    expect(await listMemories(USER_A)).toHaveLength(1);
  });

  it("rejects empty and oversized content", async () => {
    await expect(addMemory(USER_A, "   ")).rejects.toThrow(/empty/i);
    await expect(
      addMemory(USER_A, "x".repeat(MAX_MEMORY_CONTENT_CHARS + 1)),
    ).rejects.toThrow(/too long/i);
    expect(await listMemories(USER_A)).toHaveLength(0);
  });

  it("caps at MAX_MEMORIES, evicting the oldest", async () => {
    for (let i = 0; i < MAX_MEMORIES + 5; i++) {
      await addMemory(USER_A, `fact number ${i}`);
    }
    const all = await listMemories(USER_A);
    expect(all).toHaveLength(MAX_MEMORIES);
    expect(all[0]!.content).toBe(`fact number ${MAX_MEMORIES + 4}`);
    // The 5 oldest were evicted.
    const contents = all.map((m) => m.content);
    for (let i = 0; i < 5; i++) expect(contents).not.toContain(`fact number ${i}`);
  });

  it("removeMemory deletes own rows only and reports whether it removed", async () => {
    const mine = await addMemory(USER_A, "Deletable fact");
    expect(await removeMemory(USER_B, mine.id)).toBe(false); // not B's row
    expect(await listMemories(USER_A)).toHaveLength(1);
    expect(await removeMemory(USER_A, mine.id)).toBe(true);
    expect(await listMemories(USER_A)).toHaveLength(0);
    expect(await removeMemory(USER_A, "mem-does-not-exist")).toBe(false);
  });
});

// ── buildMemoryBlock ────────────────────────────────────────────────────────

describe("buildMemoryBlock", () => {
  it('returns "" when the user has no memories', async () => {
    expect(await buildMemoryBlock(USER_A)).toBe("");
  });

  it("formats a header + newest-first bullet list", async () => {
    await addMemory(USER_A, "Name is Ulrich");
    await addMemory(USER_A, "Runs Kali Linux");
    const block = await buildMemoryBlock(USER_A);
    expect(block).toBe(
      `\n\n${MEMORY_BLOCK_HEADER}\n- Runs Kali Linux\n- Name is Ulrich\n`,
    );
    expect(block.trimStart().startsWith("## What you remember about the user"))
      .toBe(true);
  });

  it(`caps the block at ${MEMORY_BLOCK_CHAR_LIMIT} chars, keeping newest`, async () => {
    // 20 × ~400-char facts ≈ 8000 chars of content — far past the budget.
    for (let i = 0; i < 20; i++) {
      await addMemory(USER_A, `fact ${String(i).padStart(2, "0")} ${"y".repeat(390)}`);
    }
    const block = await buildMemoryBlock(USER_A);
    expect(block.length).toBeLessThanOrEqual(MEMORY_BLOCK_CHAR_LIMIT);
    expect(block).toContain("fact 19"); // newest kept
    expect(block).not.toContain("fact 00"); // oldest dropped from the block
  });
});

// ── The model-callable tool ─────────────────────────────────────────────────

describe("memory tool (createMemoryTool)", () => {
  it("add saves through the data layer under the bound userId", async () => {
    const toolA = createMemoryTool(USER_A);
    const res = await exec(toolA, { action: "add", content: "Loves espresso" });
    expect(res.status).toBe("ok");
    expect(res.memory?.content).toBe("Loves espresso");

    expect((await listMemories(USER_A)).map((m) => m.content)).toEqual([
      "Loves espresso",
    ]);
    expect(await listMemories(USER_B)).toHaveLength(0); // bound to A only
  });

  it("list returns the bound user's memories with ids", async () => {
    await addMemory(USER_A, "Fact one");
    await addMemory(USER_B, "Other user's fact");
    const res = await exec(createMemoryTool(USER_A), { action: "list" });
    expect(res.status).toBe("ok");
    expect(res.count).toBe(1);
    expect(res.memories).toEqual([
      { id: expect.any(String), content: "Fact one" },
    ]);
  });

  it("remove deletes by id, scoped to the bound user", async () => {
    const row = await addMemory(USER_A, "Forget me");
    // Another user's tool cannot remove it.
    const stranger = await exec(createMemoryTool(USER_B), {
      action: "remove",
      id: row.id,
    });
    expect(stranger.status).toBe("error");
    expect(await listMemories(USER_A)).toHaveLength(1);

    const res = await exec(createMemoryTool(USER_A), {
      action: "remove",
      id: row.id,
    });
    expect(res.status).toBe("ok");
    expect(res.removedId).toBe(row.id);
    expect(await listMemories(USER_A)).toHaveLength(0);
  });

  it("missing content / missing id / unknown action → error result, no throw", async () => {
    const t = createMemoryTool(USER_A);

    const noContent = await exec(t, { action: "add" });
    expect(noContent.status).toBe("error");
    expect(noContent.error).toMatch(/content/);

    const noId = await exec(t, { action: "remove" });
    expect(noId.status).toBe("error");
    expect(noId.error).toMatch(/id/);

    const badAction = await exec(t, { action: "explode" });
    expect(badAction.status).toBe("error");
    expect(badAction.error).toMatch(/Unknown action/);

    expect(await listMemories(USER_A)).toHaveLength(0);
  });

  it("data-layer throws surface as error results (oversized content)", async () => {
    const res = await exec(createMemoryTool(USER_A), {
      action: "add",
      content: "x".repeat(MAX_MEMORY_CONTENT_CHARS + 1),
    });
    expect(res.status).toBe("error");
    expect(res.error).toMatch(/too long/i);
  });
});
