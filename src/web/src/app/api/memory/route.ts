import { and, eq, inArray } from "drizzle-orm";
import { db, schema } from "@/lib/db";
import { ensureWebSchema } from "@/lib/db/ensure-schema";
import { resolveAuth, resolveServiceUserId } from "@/lib/voice-service-auth";
import {
  applyToolCall,
  dedupEntries,
  prepareToolCall,
  renderSnapshot,
  type MemoryToolArgs,
  type MemorySnapshot,
} from "@/lib/memory/curated";

export const runtime = "nodejs";

// /api/memory — curated cloud memory for the LiveKit voice agent: the cloud
// port of the local file-backed USER.md / MEMORY.md / PROCEDURES.md stores
// (src/voice-agent/pipeline/file_memory.py + tools/memory.py). One
// web.curated_memories row per (user_id, kind), kind ∈ user|memory|procedure.
//
// GET  ?user_id=...           → the rendered snapshot: { user_block,
//   memory_block, procedure_block, snapshot_text } in the same ════-header
//   format as file_memory.py::_render_block. The voice agent fetches this
//   ONCE at session start and injects it FROZEN into the system prompt.
// POST { user_id, action, target, content?, old_text?, name? } → runs the
//   memory-tool contract (budgets, MEMORY rolling eviction, USER/PROCEDURE
//   hard-reject, injection/exfil scan, meta-paraphrase filter, dedup,
//   procedure kebab-name) and returns the tool JSON
//   { success, target, entries, entry_count, usage, message | error }.
//   Contract-level failures (validation / budget / scan) are HTTP 200 with
//   success:false — they are tool results for the model, not transport
//   errors. 4xx/5xx is reserved for auth / bad user_id / transport.
//
// Auth (both handlers): the shared dual-auth contract in
// @/lib/voice-service-auth (better-auth session OR the voice-agent proxy-JWT
// service token, sub === "voice-agent" mandatory — other subs 403; neither →
// 401). proxy.ts allowlists this path in SELF_AUTH_POST_PATTERNS +
// SELF_AUTH_GET_PATTERNS (method-scoped, next to /api/voice-memory).
//
// Concurrency: mutations run inside a Postgres transaction with
// SELECT ... FOR UPDATE on the (user_id, kind) row — the cloud equivalent of
// the local fcntl .lock file — after an INSERT ... ON CONFLICT DO NOTHING
// guarantees the row exists (curated_memories_user_kind_uniq).

const KINDS = ["user", "memory", "procedure"] as const;

async function loadSnapshot(userId: string): Promise<MemorySnapshot> {
  const rows = await db!
    .select({
      kind: schema.curatedMemories.kind,
      entries: schema.curatedMemories.entries,
    })
    .from(schema.curatedMemories)
    .where(
      and(
        eq(schema.curatedMemories.userId, userId),
        inArray(schema.curatedMemories.kind, [...KINDS]),
      ),
    )
    .limit(KINDS.length);
  const byKind: Record<string, string[]> = {};
  for (const row of rows) byKind[row.kind] = dedupEntries(row.entries);
  return renderSnapshot(
    byKind.user ?? [],
    byKind.memory ?? [],
    byKind.procedure ?? [],
  );
}

export async function GET(req: Request) {
  const auth = await resolveAuth(req);
  if (auth.kind === "forbidden") return new Response("Forbidden", { status: 403 });
  if (auth.kind === "none") return new Response("Unauthorized", { status: 401 });

  // After auth (mirrors /api/voice-memory): 'neither auth → 401' holds even
  // with persistence disabled — an empty snapshot, not an error.
  if (!db) return Response.json(renderSnapshot([], [], []));
  await ensureWebSchema();

  const url = new URL(req.url);
  let userId: string;
  if (auth.kind === "session") {
    userId = auth.userId;
  } else {
    const resolved = await resolveServiceUserId(url.searchParams.get("user_id"));
    if ("error" in resolved) return resolved.error;
    userId = resolved.userId;
  }

  return Response.json(await loadSnapshot(userId));
}

export async function POST(req: Request) {
  if (!db) {
    return Response.json({ error: "persistence disabled" }, { status: 503 });
  }
  await ensureWebSchema();

  const auth = await resolveAuth(req);
  if (auth.kind === "forbidden") return new Response("Forbidden", { status: 403 });
  if (auth.kind === "none") return new Response("Unauthorized", { status: 401 });

  let body: MemoryToolArgs & { user_id?: unknown };
  try {
    body = await req.json();
  } catch {
    return Response.json({ error: "invalid JSON body" }, { status: 400 });
  }

  let userId: string;
  if (auth.kind === "session") {
    userId = auth.userId;
  } else {
    const resolved = await resolveServiceUserId(
      typeof body.user_id === "string" ? body.user_id : undefined,
    );
    if ("error" in resolved) return resolved.error;
    userId = resolved.userId;
  }

  // Tool-layer validation (invalid target / missing params / bad procedure
  // name / unknown action) needs no DB access — reject before the transaction.
  const prepared = prepareToolCall(body);
  if ("error" in prepared) return Response.json(prepared.error);

  const memories = schema.curatedMemories;
  const rowFilter = and(
    eq(memories.userId, userId),
    eq(memories.kind, prepared.target),
  );

  if (prepared.op === "read") {
    // Read-only: no lock needed — mirrors the local read (reload + dedup).
    const rows = await db
      .select({ entries: memories.entries })
      .from(memories)
      .where(rowFilter)
      .limit(1);
    const entries = dedupEntries(rows[0]?.entries ?? []);
    return Response.json(applyToolCall(prepared, entries).response);
  }

  // Mutation: read-modify-write under SELECT ... FOR UPDATE (the cloud
  // equivalent of the local fcntl lock). Upsert-first so the row always
  // exists to lock; a concurrent first-writer race resolves at the unique
  // index and the loser's INSERT no-ops, then blocks on the row lock.
  const response = await db.transaction(async (tx) => {
    await tx
      .insert(memories)
      .values({ userId, kind: prepared.target })
      .onConflictDoNothing();
    const rows = await tx
      .select()
      .from(memories)
      .where(rowFilter)
      .limit(1)
      .for("update");
    const row = rows[0];
    if (!row) {
      // Unreachable after the upsert; fail safe rather than write blind.
      return { success: false as const, error: "memory store unavailable" };
    }
    const entries = dedupEntries(row.entries);
    const result = applyToolCall(prepared, entries);
    if (result.changed) {
      await tx
        .update(memories)
        .set({
          entries: result.entries,
          version: row.version + 1,
          updatedAt: new Date(),
        })
        .where(eq(memories.id, row.id));
    }
    return result.response;
  });

  return Response.json(response);
}
