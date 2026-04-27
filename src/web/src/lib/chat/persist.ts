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
}: {
  id?: string;
  model: string;
  firstUserText: string;
}) {
  if (!persistenceEnabled || !db) return null;
  await ensureLocalUser();

  if (id) {
    const [existing] = await db
      .select()
      .from(schema.conversations)
      .where(
        and(
          eq(schema.conversations.id, id),
          eq(schema.conversations.userId, LOCAL_USER_ID),
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
    ? { id, userId: LOCAL_USER_ID, title, model }
    : { userId: LOCAL_USER_ID, title, model };
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
}) {
  if (!db) return;
  await db.insert(schema.messages).values({
    conversationId,
    role: "assistant",
    content: [{ type: "text", text }],
    tokensIn,
    tokensOut,
    stopReason,
  });
  await db
    .update(schema.conversations)
    .set({ updatedAt: new Date() })
    .where(eq(schema.conversations.id, conversationId));
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
