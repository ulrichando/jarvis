import { and, eq } from "drizzle-orm";
import { db, schema } from "@/lib/db";
import { getUserId } from "@/lib/auth-helpers";

export const runtime = "nodejs";

export async function GET(
  req: Request,
  ctx: RouteContext<"/api/projects/[id]">,
) {
  if (!db) return new Response("Persistence disabled", { status: 503 });

  const { id } = await ctx.params;
  const userId = await getUserId(req.headers);

  const [project] = await db
    .select()
    .from(schema.projects)
    .where(
      and(
        eq(schema.projects.id, id),
        eq(schema.projects.userId, userId),
      ),
    )
    .limit(1);

  if (!project) return new Response("Not found", { status: 404 });
  return Response.json({ project });
}

export async function PATCH(
  req: Request,
  ctx: RouteContext<"/api/projects/[id]">,
) {
  if (!db) return new Response("Persistence disabled", { status: 503 });

  const { id } = await ctx.params;
  const userId = await getUserId(req.headers);
  const body = (await req.json().catch(() => null)) as {
    name?: string;
    description?: string;
    instructions?: string;
    isFavorite?: boolean;
  } | null;

  if (!body) return new Response("body required", { status: 400 });

  const patch: Record<string, unknown> = { updatedAt: new Date() };
  if (typeof body.name === "string") patch.name = body.name.trim();
  if (typeof body.description === "string") patch.description = body.description;
  if (typeof body.instructions === "string") patch.instructions = body.instructions;
  if (typeof body.isFavorite === "boolean") patch.isFavorite = body.isFavorite;

  const [row] = await db
    .update(schema.projects)
    .set(patch)
    .where(
      and(
        eq(schema.projects.id, id),
        eq(schema.projects.userId, userId),
      ),
    )
    .returning();

  if (!row) return new Response("Not found", { status: 404 });
  return Response.json({ project: row });
}

export async function DELETE(
  req: Request,
  ctx: RouteContext<"/api/projects/[id]">,
) {
  if (!db) return new Response("Persistence disabled", { status: 503 });

  const { id } = await ctx.params;
  const userId = await getUserId(req.headers);

  await db
    .delete(schema.projects)
    .where(
      and(
        eq(schema.projects.id, id),
        eq(schema.projects.userId, userId),
      ),
    );

  return new Response(null, { status: 204 });
}
