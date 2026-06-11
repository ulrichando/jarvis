import { desc, eq } from "drizzle-orm";
import { db, schema } from "@/lib/db";
import { getUserId } from "@/lib/auth-helpers";

export const runtime = "nodejs";

export async function GET(req: Request) {
  if (!db) return Response.json({ conversations: [] });
  const userId = await getUserId(req.headers);

  const rows = await db
    .select({
      id: schema.conversations.id,
      title: schema.conversations.title,
      model: schema.conversations.model,
      updatedAt: schema.conversations.updatedAt,
    })
    .from(schema.conversations)
    .where(eq(schema.conversations.userId, userId))
    .orderBy(desc(schema.conversations.updatedAt))
    .limit(100);

  return Response.json({ conversations: rows });
}
