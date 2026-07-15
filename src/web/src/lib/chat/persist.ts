import { and, eq, inArray, sql } from "drizzle-orm";
import type { UIMessage } from "ai";
import { db, persistenceEnabled, schema } from "@/lib/db";
import { estimateCostUsd } from "@/lib/ai/pricing";

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
  await db
    .update(schema.conversations)
    .set({ updatedAt: new Date() })
    .where(eq(schema.conversations.id, conversationId));
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
  await db
    .update(schema.conversations)
    .set({ updatedAt: new Date() })
    .where(eq(schema.conversations.id, conversationId));
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
export async function upsertConversations(
  userId: string,
  rows: SyncConversationInput[],
): Promise<Array<{ id: string; changeSeq: number; applied: boolean }>> {
  if (!db || rows.length === 0) return [];
  const out: Array<{ id: string; changeSeq: number; applied: boolean }> = [];
  for (const r of rows) {
    if (!r?.id || !r?.updatedAt) continue;
    const applied = await db
      .insert(schema.conversations)
      .values({
        id: r.id,
        userId,
        title: r.title ?? "New chat",
        model: r.model ?? "claude-sonnet-4-6",
        kind: r.kind ?? "chat",
        pinned: r.pinned ?? false,
        archived: r.archived ?? false,
        ...(r.createdAt ? { createdAt: new Date(r.createdAt) } : {}),
        updatedAt: new Date(r.updatedAt),
        changeSeq: sql`nextval('web.conversation_change_seq')`,
      })
      .onConflictDoUpdate({
        target: schema.conversations.id,
        set: {
          title: sql`excluded.title`,
          model: sql`excluded.model`,
          kind: sql`excluded.kind`,
          pinned: sql`excluded.pinned`,
          archived: sql`excluded.archived`,
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
): Promise<string[]> {
  if (!db || rows.length === 0) return [];
  const valid = rows.filter((m) => m?.id && m?.conversationId && (m.role === "user" || m.role === "assistant"));
  if (valid.length === 0) return [];
  const convIds = [...new Set(valid.map((m) => m.conversationId))];
  const owned = await db
    .select({ id: schema.conversations.id })
    .from(schema.conversations)
    .where(and(inArray(schema.conversations.id, convIds), eq(schema.conversations.userId, userId)));
  const ownedSet = new Set(owned.map((o) => o.id));
  const accepted = valid.filter((m) => ownedSet.has(m.conversationId));
  if (accepted.length === 0) return [];

  const inserted = await db
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

  // Bump the pull cursor on conversations that actually gained messages.
  if (inserted.length > 0) {
    const touched = [...new Set(accepted.map((m) => m.conversationId))];
    await db
      .update(schema.conversations)
      .set({ changeSeq: sql`nextval('web.conversation_change_seq')` })
      .where(inArray(schema.conversations.id, touched));
  }
  return inserted.map((r) => r.id);
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
