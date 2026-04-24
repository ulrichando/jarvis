import { desc, eq } from "drizzle-orm";
import { db, schema } from "@/lib/db";
import { LOCAL_USER_ID } from "@/lib/chat/persist";

export const runtime = "nodejs";

export async function GET() {
  if (!db) return Response.json({ conversations: [] });

  const rows = await db
    .select({
      id: schema.conversations.id,
      title: schema.conversations.title,
      model: schema.conversations.model,
      updatedAt: schema.conversations.updatedAt,
    })
    .from(schema.conversations)
    .where(eq(schema.conversations.userId, LOCAL_USER_ID))
    .orderBy(desc(schema.conversations.updatedAt))
    .limit(100);

  return Response.json({ conversations: rows });
}
