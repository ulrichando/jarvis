import { and, desc, eq } from "drizzle-orm";
import { db, schema } from "@/lib/db";
import { LOCAL_USER_ID } from "@/lib/chat/persist";
import { DEFAULT_MODEL } from "@/lib/ai/models-meta";

export const runtime = "nodejs";

export async function GET(
  _req: Request,
  ctx: RouteContext<"/api/projects/[id]/conversations">,
) {
  if (!db) return Response.json({ conversations: [] });

  const { id } = await ctx.params;

  const rows = await db
    .select({
      id: schema.conversations.id,
      title: schema.conversations.title,
      model: schema.conversations.model,
      updatedAt: schema.conversations.updatedAt,
    })
    .from(schema.conversations)
    .where(
      and(
        eq(schema.conversations.userId, LOCAL_USER_ID),
        eq(schema.conversations.projectId, id),
      ),
    )
    .orderBy(desc(schema.conversations.updatedAt))
    .limit(100);

  return Response.json({ conversations: rows });
}

export async function POST(
  req: Request,
  ctx: RouteContext<"/api/projects/[id]/conversations">,
) {
  if (!db) return new Response("Persistence disabled", { status: 503 });

  const { id: projectId } = await ctx.params;

  // Verify the project belongs to the local user before linking.
  const [project] = await db
    .select({ id: schema.projects.id })
    .from(schema.projects)
    .where(
      and(
        eq(schema.projects.id, projectId),
        eq(schema.projects.userId, LOCAL_USER_ID),
      ),
    )
    .limit(1);
  if (!project) return new Response("Project not found", { status: 404 });

  const body = (await req.json().catch(() => null)) as {
    title?: string;
    model?: string;
  } | null;

  const [created] = await db
    .insert(schema.conversations)
    .values({
      userId: LOCAL_USER_ID,
      projectId,
      title: body?.title?.trim() || "New chat",
      model: body?.model || DEFAULT_MODEL,
    })
    .returning();

  return Response.json({ conversation: created }, { status: 201 });
}
