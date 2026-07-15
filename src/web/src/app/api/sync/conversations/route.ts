import { and, asc, gt, eq, sql } from "drizzle-orm";
import { db, schema } from "@/lib/db";
import { ensureWebSchema } from "@/lib/db/ensure-schema";
import { requireUserIdOrSharedLocal, Unauthenticated } from "@/lib/auth-helpers";

export const runtime = "nodejs";

// Two-way mobile sync — the web→phone pull leg (metadata slice). Returns the
// caller's conversations whose change_seq advanced past `since`, oldest-cursor
// first, so the phone can merge metadata (LWW by updatedAt) and learn which
// conversations to pull messages for. Bridge-token auth (proxy SELF_AUTH_GET).
export async function GET(req: Request) {
  if (!db) return Response.json({ conversations: [], nextSince: 0, hasMore: false });
  await ensureWebSchema();

  let userId: string;
  try {
    userId = await requireUserIdOrSharedLocal(req.headers);
  } catch (e) {
    if (e instanceof Unauthenticated) return new Response("Unauthorized", { status: 401 });
    throw e;
  }

  const url = new URL(req.url);
  const since = Math.max(0, Number(url.searchParams.get("since") ?? "0") || 0);
  const limit = Math.min(500, Math.max(1, Number(url.searchParams.get("limit") ?? "200") || 200));

  // `updated_at`/`created_at` are `timestamp` WITHOUT tz, stored as session
  // local wall-clock but read back by node-pg as UTC — a skew. Reinterpret in
  // the session tz so the phone gets the true instant (matches /api/conversations
  // and the value the phone originally pushed → LWW stays consistent).
  const rows = await db
    .select({
      id: schema.conversations.id,
      title: schema.conversations.title,
      model: schema.conversations.model,
      kind: schema.conversations.kind,
      pinned: schema.conversations.pinned,
      archived: schema.conversations.archived,
      createdAt: sql<string>`(${schema.conversations.createdAt} AT TIME ZONE current_setting('TimeZone'))`,
      updatedAt: sql<string>`(${schema.conversations.updatedAt} AT TIME ZONE current_setting('TimeZone'))`,
      changeSeq: schema.conversations.changeSeq,
      // The phone skips re-downloading a thread whose count already matches, so
      // a self-push echo / metadata-only change doesn't re-pull every message.
      // NB: reference the outer column by its explicit qualified name — drizzle
      // renders an interpolated ${schema.conversations.id} here as a bare "id",
      // which Postgres binds to web.messages.id inside the subquery → always 0.
      messageCount: sql<number>`(SELECT count(*)::int FROM web.messages m WHERE m.conversation_id = web.conversations.id AND m.role IN ('user','assistant'))`,
    })
    .from(schema.conversations)
    .where(
      and(
        eq(schema.conversations.userId, userId),
        gt(schema.conversations.changeSeq, since),
      ),
    )
    .orderBy(asc(schema.conversations.changeSeq))
    .limit(limit);

  const nextSince = rows.length ? Number(rows[rows.length - 1].changeSeq) : since;
  return Response.json({
    conversations: rows.map((r) => ({ ...r, changeSeq: Number(r.changeSeq), messageCount: Number(r.messageCount) })),
    nextSince,
    hasMore: rows.length === limit,
  });
}
