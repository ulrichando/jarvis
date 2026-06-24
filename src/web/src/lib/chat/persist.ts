import { and, eq } from "drizzle-orm";
import type { UIMessage } from "ai";
import { db, persistenceEnabled, schema } from "@/lib/db";

export const LOCAL_USER_ID = "00000000-0000-0000-0000-000000000001";
export const LOCAL_USER_EMAIL = "local@jarvis";

function extractText(parts: UIMessage["parts"]): string {
  return parts.map((p) => (p.type === "text" ? p.text : "")).join("");
}

async function ensureLocalUser() {
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
}: {
  id?: string;
  model: string;
  firstUserText: string;
  /** Owner of the conversation. Defaults to the local user (auth-disabled). */
  userId?: string;
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

  const title = firstUserText.slice(0, 80).trim() || "New chat";

  // Don't pass `id` when it's null/undefined — postgres rejects it as
  // a not-null violation. Omitting lets the column's gen_random_uuid()
  // default fill in. When id IS provided (e.g. existing chat), pass
  // it through so the row is created with the caller's id.
  const values = id
    ? { id, userId, title, model }
    : { userId, title, model };
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
