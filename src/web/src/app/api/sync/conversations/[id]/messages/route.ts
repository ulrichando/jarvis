import { and, asc, eq, inArray, isNull, sql } from "drizzle-orm";
import { db, schema } from "@/lib/db";
import { ensureWebSchema } from "@/lib/db/ensure-schema";
import { requireUserIdOrSharedLocal, Unauthenticated } from "@/lib/auth-helpers";

export const runtime = "nodejs";

// Two-way mobile sync — the web→phone pull leg (message slice). Returns the
// user/assistant rows of a conversation the caller owns, in DB-native shape
// ({id, role, parts, createdAt}) — NOT via toUIMessages, so nothing is dropped.
// The phone unions these by uuid (insert-ignore), so returning the full set is
// idempotent; ponytail: no `after` cursor in v1 (avoids the timestamp-tz edge),
// re-pull cost is bounded by how often a thread changes.
export async function GET(req: Request, ctx: { params: Promise<{ id: string }> }) {
  if (!db) return Response.json({ messages: [] });
  await ensureWebSchema();

  let userId: string;
  try {
    userId = await requireUserIdOrSharedLocal(req.headers);
  } catch (e) {
    if (e instanceof Unauthenticated) return new Response("Unauthorized", { status: 401 });
    throw e;
  }

  const { id } = await ctx.params;
  // 404 if the conversation isn't the caller's (scoped select).
  const [owned] = await db
    .select({ id: schema.conversations.id })
    .from(schema.conversations)
    .where(and(eq(schema.conversations.id, id), eq(schema.conversations.userId, userId)))
    .limit(1);
  if (!owned) return new Response("Not found", { status: 404 });

  const rows = await db
    .select({
      id: schema.messages.id,
      role: schema.messages.role,
      parts: schema.messages.content,
      tokensIn: schema.messages.tokensIn,
      tokensOut: schema.messages.tokensOut,
      stopReason: schema.messages.stopReason,
      createdAt: sql<string>`(${schema.messages.createdAt} AT TIME ZONE current_setting('TimeZone'))`,
    })
    .from(schema.messages)
    .where(
      and(
        eq(schema.messages.conversationId, id),
        inArray(schema.messages.role, ["user", "assistant"]),
        isNull(schema.messages.deletedAt), // exclude soft-deleted messages
      ),
    )
    .orderBy(asc(schema.messages.createdAt));

  return Response.json({ messages: rows });
}
