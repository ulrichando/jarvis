import { and, asc, eq } from "drizzle-orm";
import { db, schema } from "@/lib/db";
import { LOCAL_USER_ID, toUIMessages } from "@/lib/chat/persist";

export const runtime = "nodejs";

export async function GET(_req: Request, ctx: RouteContext<"/api/conversations/[id]">) {
  if (!db) return new Response("Persistence disabled", { status: 503 });

  const { id } = await ctx.params;

  const [conversation] = await db
    .select()
    .from(schema.conversations)
    .where(
      and(
        eq(schema.conversations.id, id),
        eq(schema.conversations.userId, LOCAL_USER_ID),
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
  _req: Request,
  ctx: RouteContext<"/api/conversations/[id]">,
) {
  if (!db) return new Response("Persistence disabled", { status: 503 });

  const { id } = await ctx.params;

  await db
    .delete(schema.conversations)
    .where(
      and(
        eq(schema.conversations.id, id),
        eq(schema.conversations.userId, LOCAL_USER_ID),
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
  const body = (await req.json().catch(() => ({}))) as {
    title?: unknown;
  };
  const title =
    typeof body.title === "string" ? body.title.trim().slice(0, 200) : "";
  if (!title) return new Response("Missing title", { status: 400 });

  await db
    .update(schema.conversations)
    .set({ title })
    .where(
      and(
        eq(schema.conversations.id, id),
        eq(schema.conversations.userId, LOCAL_USER_ID),
      ),
    );

  return Response.json({ ok: true });
}
