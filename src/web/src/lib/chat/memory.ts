// Persistent chat memory — the data layer behind the model-callable `memory`
// tool (src/lib/tools/memory.ts). ChatGPT/Claude-style: the model saves short
// durable facts about the user (name, preferences, projects), and every chat
// turn gets the full set injected into the system prompt via
// buildMemoryBlock(). One `web.user_memories` row per fact, per-user.
//
// Deliberately DISTINCT from src/lib/memory/curated.ts (the voice agent's
// kinds-based curated stores behind /api/memory) — this is the web chat's
// flat fact list, fully model-managed.
//
// Conventions follow persist.ts: null-`db` tolerated (reads degrade to
// empty, writes throw a clear message the tool converts to an error result),
// and ensureWebSchema() runs before any table access so a cold process /
// existing production DB self-provisions the table (drizzle-push-managed DB,
// no runtime migration flow).

import { and, desc, eq, inArray } from "drizzle-orm";
import { db, schema } from "@/lib/db";
import { ensureWebSchema } from "@/lib/db/ensure-schema";
import type { UserMemory } from "@/lib/db/schema";

const { userMemories } = schema;

/** Rolling cap on stored facts per user — oldest evicted on overflow. */
export const MAX_MEMORIES = 50;
/** Per-fact length cap: memories are short, single-fact sentences. */
export const MAX_MEMORY_CONTENT_CHARS = 500;
/** Hard cap on the injected system-prompt block, including its header. */
export const MEMORY_BLOCK_CHAR_LIMIT = 2000;
export const MEMORY_BLOCK_HEADER = "## What you remember about the user";

/** Whitespace-collapsed, case-folded form used for dedup comparison. */
function normalized(content: string): string {
  return content.trim().replace(/\s+/g, " ").toLowerCase();
}

async function listAllNewestFirst(userId: string): Promise<UserMemory[]> {
  if (!db) return [];
  await ensureWebSchema();
  return db
    .select()
    .from(userMemories)
    .where(eq(userMemories.userId, userId))
    .orderBy(desc(userMemories.createdAt));
}

/**
 * Save one durable fact for the user. Content is trimmed and
 * whitespace-collapsed (facts render as single "- …" bullet lines).
 * Case/whitespace-insensitive duplicates return the existing row instead of
 * inserting. On overflow past MAX_MEMORIES the oldest rows are evicted
 * (rolling store). Throws on empty/oversized content or missing persistence —
 * the tool layer converts throws to error results.
 */
export async function addMemory(
  userId: string,
  content: string,
): Promise<UserMemory> {
  const trimmed = (content ?? "").trim().replace(/\s+/g, " ");
  if (!trimmed) throw new Error("Memory content is empty.");
  if (trimmed.length > MAX_MEMORY_CONTENT_CHARS) {
    throw new Error(
      `Memory content too long (${trimmed.length} chars; max ${MAX_MEMORY_CONTENT_CHARS}). Save a shorter, single-fact summary.`,
    );
  }
  if (!db) {
    throw new Error(
      "Memory persistence is not configured (DATABASE_URL is unset).",
    );
  }
  await ensureWebSchema();

  const existing = await listAllNewestFirst(userId);
  const dup = existing.find((m) => normalized(m.content) === normalized(trimmed));
  if (dup) return dup;

  const [row] = await db
    .insert(userMemories)
    .values({ userId, content: trimmed })
    .returning();

  // Rolling cap: evict the oldest rows beyond MAX_MEMORIES.
  const all = await listAllNewestFirst(userId);
  if (all.length > MAX_MEMORIES) {
    const overflowIds = all.slice(MAX_MEMORIES).map((m) => m.id);
    await db
      .delete(userMemories)
      .where(
        and(
          eq(userMemories.userId, userId),
          inArray(userMemories.id, overflowIds),
        ),
      );
  }
  return row!;
}

/** The user's saved memories, newest first, capped at MAX_MEMORIES. */
export async function listMemories(userId: string): Promise<UserMemory[]> {
  const all = await listAllNewestFirst(userId);
  return all.slice(0, MAX_MEMORIES);
}

/**
 * Delete one memory by id, scoped to the owning user (a bound tool can never
 * delete another user's row). Returns whether a row was actually removed.
 */
export async function removeMemory(
  userId: string,
  id: string,
): Promise<boolean> {
  if (!db) {
    throw new Error(
      "Memory persistence is not configured (DATABASE_URL is unset).",
    );
  }
  await ensureWebSchema();
  const deleted = await db
    .delete(userMemories)
    .where(and(eq(userMemories.id, id), eq(userMemories.userId, userId)))
    .returning();
  return deleted.length > 0;
}

/**
 * Compact system-prompt block of the user's saved memories, for
 * `finalSystem += await buildMemoryBlock(userId)` in the chat route —
 * mirrors readGlobalKnowledgeBlock: "" when there is nothing to inject.
 * Newest first; total length (header included) never exceeds
 * MEMORY_BLOCK_CHAR_LIMIT. Never throws — memory must not break the chat
 * hot path.
 */
export async function buildMemoryBlock(userId: string): Promise<string> {
  let memories: UserMemory[];
  try {
    memories = await listMemories(userId);
  } catch (err) {
    console.error("[chat-memory] buildMemoryBlock failed:", err);
    return "";
  }
  if (memories.length === 0) return "";

  let block = `\n\n${MEMORY_BLOCK_HEADER}\n`;
  let added = 0;
  for (const m of memories) {
    const line = `- ${m.content}\n`;
    if (block.length + line.length > MEMORY_BLOCK_CHAR_LIMIT) break;
    block += line;
    added++;
  }
  return added === 0 ? "" : block;
}
