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
        eq(schema.conversations.userId, userId),
      ),
    );

  return Response.json({ ok: true });
}
