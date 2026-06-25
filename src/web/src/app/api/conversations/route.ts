import { desc, eq, sql } from "drizzle-orm";
import { db, schema } from "@/lib/db";
import { requireUserId, Unauthenticated } from "@/lib/auth-helpers";

export const runtime = "nodejs";

export async function GET(req: Request) {
  if (!db) return Response.json({ conversations: [] });
  let userId: string;
  try {
    userId = await requireUserId(req.headers);
  } catch (e) {
    if (e instanceof Unauthenticated) return new Response("Unauthorized", { status: 401 });
    throw e;
  }

  const rows = await db
    .select({
      id: schema.conversations.id,
      title: schema.conversations.title,
      pinned: schema.conversations.pinned,
      model: schema.conversations.model,
      // `updated_at` is `timestamp` WITHOUT time zone, written as the PG
      // session's local wall-clock (America/New_York) but parsed by node-pg
      // as UTC → a multi-hour skew on every relative time. Re-interpret it in
      // the session tz so the client receives a correct `timestamptz` instant.
      // Fixes the sidebar's times too (same hook).
      updatedAt: sql<string>`(${schema.conversations.updatedAt} AT TIME ZONE current_setting('TimeZone'))`,
      projectId: schema.conversations.projectId,
      // LEFT JOIN: null for chats not attached to a project. Lets the
      // /chats page render the project tag + "Filter by project" without
      // a second round-trip. Additive — existing consumers ignore it.
      projectName: schema.projects.name,
    })
    .from(schema.conversations)
    .leftJoin(
      schema.projects,
      eq(schema.conversations.projectId, schema.projects.id),
    )
    .where(eq(schema.conversations.userId, userId))
    .orderBy(desc(schema.conversations.updatedAt))
    .limit(100);

  return Response.json({ conversations: rows });
}
