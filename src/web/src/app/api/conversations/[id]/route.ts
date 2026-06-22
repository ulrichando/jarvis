import { and, asc, eq } from "drizzle-orm";
import { db, schema } from "@/lib/db";
import { toUIMessages } from "@/lib/chat/persist";
import { getUserId } from "@/lib/auth-helpers";

export const runtime = "nodejs";

export async function GET(req: Request, ctx: RouteContext<"/api/conversations/[id]">) {
  if (!db) return new Response("Persistence disabled", { status: 503 });

  const { id } = await ctx.params;
  const userId = await getUserId(req.headers);

  const [conversation] = await db
    .select()
    .from(schema.conversations)
    .where(
      and(
        eq(schema.conversations.id, id),
        eq(schema.conversations.userId, userId),
      ),
    )
    .limit(1);

  if (!conversation) return new Response("Not found", { status: 404 });

  const rows = await db
    .select()
    .from(schema.messages)
    .where(eq(schema.messages.conversationId, id))
    .orderBy(asc(schema.messages.createdAt));

  return Response.json({
    conversation,
    messages: toUIMessages(rows),
  });
}

export async function DELETE(
  req: Request,
  ctx: RouteContext<"/api/conversations/[id]">,
) {
  if (!db) return new Response("Persistence disabled", { status: 503 });

  const { id } = await ctx.params;
  const userId = await getUserId(req.headers);

  await db
    .delete(schema.conversations)
    .where(
      and(
        eq(schema.conversations.id, id),
        eq(schema.conversations.userId, userId),
      ),
    );

  return new Response(null, { status: 204 });
}

export async function PATCH(
  req: Request,
  ctx: RouteContext<"/api/conversations/[id]">,
) {
  if (!db) return new Response("Persistence disabled", { status: 503 });

  const { id } = await ctx.params;
  const userId = await getUserId(req.headers);
  const body = (await req.json().catch(() => ({}))) as {
    title?: unknown;
    pinned?: unknown;
    projectId?: unknown;
  };
  const updates: { title?: string; pinned?: boolean; projectId?: string | null } = {};
  if (typeof body.title === "string") {
    const t = body.title.trim().slice(0, 200);
    if (t) updates.title = t;
  }
  if (typeof body.pinned === "boolean") updates.pinned = body.pinned;
  // null / "" → detach from project; a string id → attach.
  if (body.projectId === null || typeof body.projectId === "string") {
    updates.projectId = (body.projectId as string | null) || null;
  }
  if (Object.keys(updates).length === 0) {
    return new Response(
      "Nothing to update (title, pinned, or projectId required)",
      { status: 400 },
    );
  }

  await db
    .update(schema.conversations)
    .set(updates)
    .where(
      and(
        eq(schema.conversations.id, id),
        eq(schema.conversations.userId, userId),
      ),
    );

  return Response.json({ ok: true });
}
