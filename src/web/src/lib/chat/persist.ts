import { and, eq, inArray, isNull, sql } from "drizzle-orm";
import type { UIMessage } from "ai";
import { db, persistenceEnabled, schema } from "@/lib/db";
import { ensureWebSchema } from "@/lib/db/ensure-schema";
import { estimateCostUsd } from "@/lib/ai/pricing";

/**
 * Bump a conversation's updatedAt + change_seq — the latter is the mobile-sync
 * pull cursor, so a web-authored turn/edit becomes visible to the phone's
 * `?since=` slice. ensureWebSchema (no-op after boot) guarantees the sequence
 * exists before nextval so this never breaks the chat path on a cold process.
 */
async function touchConversation(conversationId: string) {
  if (!db) return;
  await ensureWebSchema();
  await db
    .update(schema.conversations)
    .set({
      updatedAt: new Date(),
      changeSeq: sql`nextval('web.conversation_change_seq')`,
    })
    .where(eq(schema.conversations.id, conversationId));
}

export const LOCAL_USER_ID = "00000000-0000-0000-0000-000000000001";
export const LOCAL_USER_EMAIL = "local@jarvis";

function extractText(parts: UIMessage["parts"]): string {
  return parts.map((p) => (p.type === "text" ? p.text : "")).join("");
}

export async function ensureLocalUser() {
  if (!db) return;
  const existing = await db
    .select({ id: schema.users.id })
    .from(schema.users)
    .where(eq(schema.users.id, LOCAL_USER_ID))
    .limit(1);
  if (existing.length === 0) {
    await db
      .insert(schema.users)
      .values({
        id: LOCAL_USER_ID,
        email: LOCAL_USER_EMAIL,
        name: "You",
      })
      .onConflictDoNothing();
  }
}

export async function ensureConversation({
  id,
  model,
  firstUserText,
  userId = LOCAL_USER_ID,
  kind,
  title: titleOverride,
}: {
  id?: string;
  model: string;
  firstUserText: string;
  /** Owner of the conversation. Defaults to the local user (auth-disabled). */
  userId?: string;
  /** "task" marks scheduled-task setup/run sessions so the sidebar shows a
   *  task icon. Omit (→ column default "chat") for normal chats. */
  kind?: "task" | "chat";
  /** Explicit title override (e.g. "Daily brief" for a template task).
   *  Falls back to firstUserText when absent. */
  title?: string;
}) {
  if (!persistenceEnabled || !db) return null;
  // This SELECT/INSERT now references conversations.change_seq (added to the
  // schema for mobile sync), so guarantee the column exists first — otherwise a
  // cold process whose FIRST request is a chat (scheduled task / voice agent)
  // 500s on an existing DB that hasn't been touched by a sync/settings route yet.
  await ensureWebSchema();
  if (userId === LOCAL_USER_ID) await ensureLocalUser();

  if (id) {
    const [existing] = await db
      .select()
      .from(schema.conversations)
      .where(
        and(
          eq(schema.conversations.id, id),
          eq(schema.conversations.userId, userId),
        ),
      )
      .limit(1);
    if (existing) return existing;
  }

  const title =
    titleOverride?.trim() || firstUserText.slice(0, 80).trim() || "New chat";

  // Don't pass `id` when it's null/undefined — postgres rejects it as
  // a not-null violation. Omitting lets the column's gen_random_uuid()
  // default fill in. When id IS provided (e.g. existing chat), pass
  // it through so the row is created with the caller's id.
  const values = id
    ? { id, userId, title, model, ...(kind ? { kind } : {}) }
    : { userId, title, model, ...(kind ? { kind } : {}) };
  const [created] = await db
    .insert(schema.conversations)
    .values(values)
    .returning();
  return created;
}

export async function saveUserMessage({
  conversationId,
  message,
}: {
  conversationId: string;
  message: UIMessage;
}) {
  if (!db) return;
  await db.insert(schema.messages).values({
    conversationId,
    role: "user",
    content: message.parts,
  });
  await touchConversation(conversationId);
}

export async function saveAssistantMessage({
  conversationId,
  text,
  tokensIn,
  tokensOut,
  stopReason,
}: {
  conversationId: string;
  text: string;
  tokensIn?: number;
  tokensOut?: number;
  stopReason?: string;
}): Promise<string | null> {
  if (!db) return null;
  // Return the inserted id so callers (e.g. artifact persistence in
  // chat/route.ts onFinish) can attribute artifact versions to this turn.
  // Existing callers that ignore the return are unaffected.
  const [row] = await db
    .insert(schema.messages)
    .values({
      conversationId,
      role: "assistant",
      content: [{ type: "text", text }],
      tokensIn,
      tokensOut,
      stopReason,
    })
    .returning({ id: schema.messages.id });
  await touchConversation(conversationId);
  return row?.id ?? null;
}

/**
 * One usage_events row per completed model turn — the metering behind
 * Settings → Usage. Independent of message persistence so workspace/design
 * turns without a conversation still count. Never throws: metering must
 * not break the chat path.
 */
export async function recordUsageEvent({
  userId,
  conversationId,
  model,
  tokensIn,
  tokensOut,
  cacheReadTokens,
}: {
  userId: string;
  conversationId?: string | null;
  model: string;
  tokensIn?: number;
  tokensOut?: number;
  cacheReadTokens?: number;
}) {
  if (!db) return;
  const inTok = tokensIn ?? 0;
  const outTok = tokensOut ?? 0;
  // Stored cost is the estimate at write time (audit trail); aggregates
  // re-price from tokens at read time so pricing fixes apply retroactively.
  const cost = estimateCostUsd(model, inTok, outTok);
  try {
    await db.insert(schema.usageEvents).values({
      userId,
      conversationId: conversationId ?? null,
      model,
      tokensIn: inTok,
      tokensOut: outTok,
      cacheReadTokens: cacheReadTokens ?? 0,
      costUsd: cost != null ? cost.toFixed(6) : null,
    });
  } catch (err) {
    console.error("[usage] failed to record usage event", err);
  }
}

/**
 * Update the most recent assistant message of a conversation if the
 * client's version of that message has additional text the server's DB
 * row doesn't have yet. This is how we persist client-side appends like
 * the synthetic <boltActionResults> block — the model returns its raw
 * text via streamText, but the chat layer enriches it AFTER actions
 * run, and that enrichment lives only in client state until the user
 * fires a follow-up turn carrying the enriched message in the request.
 *
 * Safety: we only overwrite when the client text is a strict superset
 * (starts with the DB text) — prevents accidental loss if the client
 * somehow sends a TRUNCATED version (race, bug). If the texts diverge,
 * we keep the DB version untouched.
 */
export async function maybeUpdateLastAssistantMessage({
  conversationId,
  candidate,
}: {
  conversationId: string;
  candidate: UIMessage;
}) {
  if (!db) return;
  if (candidate.role !== "assistant") return;
  const candidateText = extractText(candidate.parts);
  if (!candidateText) return;
  const [latest] = await db
    .select()
    .from(schema.messages)
    .where(
      and(
        eq(schema.messages.conversationId, conversationId),
        eq(schema.messages.role, "assistant"),
      ),
    )
    .orderBy(schema.messages.createdAt)
    .limit(50);
  // Use the LAST row by createdAt (drizzle's orderBy default is asc);
  // grabbing 50 is enough headroom and avoids a separate `desc` import
  // for now.
  if (!latest) return;
  const rows = await db
    .select()
    .from(schema.messages)
    .where(
      and(
        eq(schema.messages.conversationId, conversationId),
        eq(schema.messages.role, "assistant"),
      ),
    );
  if (rows.length === 0) return;
  const last = rows[rows.length - 1];
  const dbParts = Array.isArray(last.content)
    ? (last.content as UIMessage["parts"])
    : [{ type: "text" as const, text: String(last.content ?? "") }];
  const dbText = extractText(dbParts);
  // No change → no-op.
  if (candidateText === dbText) return;
  // Only enrich (extend) — never overwrite divergent content. The
  // client text must START WITH the DB text for the update to be safe.
  if (!candidateText.startsWith(dbText)) return;
  await db
    .update(schema.messages)
    .set({ content: candidate.parts })
    .where(eq(schema.messages.id, last.id));
}

// ── Two-way mobile sync — idempotent, phone-owned ids ───────────────────────
// The phone mints every id (conversation + message) and pushes finalized rows.
// Conversations are whole-row LWW by updatedAt; messages are append-only union
// by id. Every applied write bumps the conversation's change_seq (the pull
// cursor). Callers must have passed the route's IDOR guard.

export type SyncConversationInput = {
  id: string;
  title?: string;
  model?: string;
  kind?: string;
  pinned?: boolean;
  archived?: boolean;
  createdAt?: string; // ISO
  updatedAt: string; // ISO — the LWW key
};

export type SyncMessageInput = {
  id: string;
  conversationId: string;
  role: "user" | "assistant";
  parts: unknown; // UIMessage parts, stored verbatim into content jsonb
  clientCreatedAt?: string; // ISO → messages.created_at (intra-conversation order)
  tokensIn?: number;
  tokensOut?: number;
  stopReason?: string | null;
};

/**
 * Upsert conversations from a device. Whole-row LWW by updatedAt, guarded so a
 * stale client can't clobber a newer server row (and can't touch another user's
 * row). Bumps change_seq on every applied write. Returns per-id apply results
 * so the phone can advance its cursor / know what to re-pull.
 */
// A drizzle db handle or an in-flight transaction — lets the push route run both
// upserts in ONE transaction so a message insert and its change_seq bump can't
// commit apart (which would strand the bump on a mid-batch failure/retry).
type DbExecutor =
  | NonNullable<typeof db>
  | Parameters<Parameters<NonNullable<typeof db>["transaction"]>[0]>[0];

export async function upsertConversations(
  userId: string,
  rows: SyncConversationInput[],
  exec: DbExecutor = db as DbExecutor,
): Promise<Array<{ id: string; changeSeq: number; applied: boolean }>> {
  if (!db || rows.length === 0) return [];
  const out: Array<{ id: string; changeSeq: number; applied: boolean }> = [];
  for (const r of rows) {
    if (!r?.id || !r?.updatedAt) continue;
    // Skip an unparseable timestamp rather than inserting an Invalid Date (→ 500).
    const updatedAt = new Date(r.updatedAt);
    if (Number.isNaN(updatedAt.getTime())) continue;
    const createdAt = r.createdAt ? new Date(r.createdAt) : null;
    // Restrict kind to chat/task on create — never let a client inject 'voice',
    // which would trip conversations_voice_user_uniq and 500 the whole batch.
    const kind = r.kind === "task" ? "task" : "chat";
    const applied = await exec
      .insert(schema.conversations)
      .values({
        id: r.id,
        userId,
        title: r.title ?? "New chat",
        model: r.model ?? "claude-sonnet-4-6",
        kind,
        pinned: r.pinned ?? false,
        archived: r.archived ?? false,
        ...(createdAt && !Number.isNaN(createdAt.getTime()) ? { createdAt } : {}),
        updatedAt,
        changeSeq: sql`nextval('web.conversation_change_seq')`,
      })
      .onConflictDoUpdate({
        target: schema.conversations.id,
        // Only fields the phone actually owns. kind/archived are deliberately
        // NOT overwritten — the phone doesn't store them, so echoing its
        // hardcoded "chat"/false would silently strip a server-side task/voice/
        // archived flag (and orphan voice-memory's find-or-create thread).
        set: {
          title: sql`excluded.title`,
          model: sql`excluded.model`,
          pinned: sql`excluded.pinned`,
          updatedAt: sql`excluded.updated_at`,
          changeSeq: sql`nextval('web.conversation_change_seq')`,
        },
        // LWW + ownership: only overwrite when the client row is strictly newer
        // AND the existing row is this user's (defense-in-depth vs the route guard).
        setWhere: sql`excluded.updated_at > ${schema.conversations.updatedAt} and ${schema.conversations.userId} = ${userId}`,
      })
      .returning({ id: schema.conversations.id, changeSeq: schema.conversations.changeSeq });
    if (applied[0]) out.push({ id: applied[0].id, changeSeq: applied[0].changeSeq, applied: true });
    else out.push({ id: r.id, changeSeq: 0, applied: false }); // LWW-rejected → phone should pull
  }
  return out;
}

/**
 * Insert pushed messages (append-only union). Only accepts messages whose
 * conversation is owned by `userId`. `ON CONFLICT (id) DO NOTHING` makes re-push
 * a no-op. Bumps each touched conversation's change_seq. Returns the ids that
 * were actually inserted (new to the server).
 */
export async function upsertMessages(
  userId: string,
  rows: SyncMessageInput[],
  exec: DbExecutor = db as DbExecutor,
): Promise<string[]> {
  if (!db || rows.length === 0) return [];
  const valid = rows.filter((m) => m?.id && m?.conversationId && (m.role === "user" || m.role === "assistant"));
  if (valid.length === 0) return [];
  const convIds = [...new Set(valid.map((m) => m.conversationId))];
  const owned = await exec
    .select({ id: schema.conversations.id })
    .from(schema.conversations)
    .where(and(inArray(schema.conversations.id, convIds), eq(schema.conversations.userId, userId)));
  const ownedSet = new Set(owned.map((o) => o.id));
  const accepted = valid.filter((m) => ownedSet.has(m.conversationId));
  if (accepted.length === 0) return [];

  const inserted = await exec
    .insert(schema.messages)
    .values(
      accepted.map((m) => ({
        id: m.id,
        conversationId: m.conversationId,
        role: m.role,
        content: m.parts as UIMessage["parts"],
        ...(m.clientCreatedAt ? { createdAt: new Date(m.clientCreatedAt) } : {}),
        tokensIn: m.tokensIn ?? null,
        tokensOut: m.tokensOut ?? null,
        stopReason: m.stopReason ?? null,
      })),
    )
    .onConflictDoNothing({ target: schema.messages.id })
    .returning({ id: schema.messages.id });

  // Bump the pull cursor ONLY on conversations that actually gained a row (not
  // every conversation in the batch) — avoids cursor churn that would make other
  // devices re-download unchanged threads.
  const insertedIds = new Set(inserted.map((r) => r.id));
  const touched = [...new Set(accepted.filter((m) => insertedIds.has(m.id)).map((m) => m.conversationId))];
  if (touched.length > 0) {
    await exec
      .update(schema.conversations)
      .set({ changeSeq: sql`nextval('web.conversation_change_seq')` })
      .where(inArray(schema.conversations.id, touched));
  }
  return inserted.map((r) => r.id);
}

/**
 * Soft-delete conversations pushed as tombstones from a device. Sets deleted_at
 * + bumps change_seq so the delete propagates via the pull. Owned-only,
 * idempotent (skips already-deleted rows).
 */
export async function softDeleteConversations(
  userId: string,
  ids: string[],
  exec: DbExecutor = db as DbExecutor,
): Promise<void> {
  if (!db || ids.length === 0) return;
  await exec
    .update(schema.conversations)
    .set({ deletedAt: sql`now()`, changeSeq: sql`nextval('web.conversation_change_seq')` })
    .where(
      and(
        inArray(schema.conversations.id, ids),
        eq(schema.conversations.userId, userId),
        isNull(schema.conversations.deletedAt),
      ),
    );
}

/**
 * Soft-delete messages pushed as tombstones. Owned-only (via the parent
 * conversation), idempotent; bumps each affected conversation's change_seq so
 * the deletion is picked up by the pull (which excludes deleted messages).
 */
export async function softDeleteMessages(
  userId: string,
  ids: string[],
  exec: DbExecutor = db as DbExecutor,
): Promise<void> {
  if (!db || ids.length === 0) return;
  const rows = await exec
    .select({ id: schema.messages.id, conversationId: schema.messages.conversationId })
    .from(schema.messages)
    .innerJoin(schema.conversations, eq(schema.messages.conversationId, schema.conversations.id))
    .where(
      and(
        inArray(schema.messages.id, ids),
        eq(schema.conversations.userId, userId),
        isNull(schema.messages.deletedAt),
      ),
    );
  if (rows.length === 0) return;
  await exec
    .update(schema.messages)
    .set({ deletedAt: sql`now()` })
    .where(inArray(schema.messages.id, rows.map((r) => r.id)));
  await exec
    .update(schema.conversations)
    .set({ changeSeq: sql`nextval('web.conversation_change_seq')` })
    .where(inArray(schema.conversations.id, [...new Set(rows.map((r) => r.conversationId))]));
}

export function toUIMessages(
  rows: Array<typeof schema.messages.$inferSelect>,
): UIMessage[] {
  return rows
    .filter((r) => r.role === "user" || r.role === "assistant")
    .map((r) => ({
      id: r.id,
      role: r.role as "user" | "assistant",
      parts: Array.isArray(r.content)
        ? (r.content as UIMessage["parts"])
        : [{ type: "text", text: String(r.content ?? "") }],
    }));
}

export { extractText };
