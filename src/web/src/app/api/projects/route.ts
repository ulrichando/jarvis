import { desc, eq } from "drizzle-orm";
import { db, schema } from "@/lib/db";
import { requireUserId, Unauthenticated } from "@/lib/auth-helpers";

export const runtime = "nodejs";

export async function GET(req: Request) {
  if (!db) return Response.json({ projects: [] });
  let userId: string;
  try {
    userId = await requireUserId(req.headers);
  } catch (e) {
    if (e instanceof Unauthenticated) return new Response("Unauthorized", { status: 401 });
    throw e;
  }

  const rows = await db
    .select({
      id: schema.projects.id,
      name: schema.projects.name,
      description: schema.projects.description,
      badge: schema.projects.badge,
      isFavorite: schema.projects.isFavorite,
      createdAt: schema.projects.createdAt,
      updatedAt: schema.projects.updatedAt,
    })
    .from(schema.projects)
    .where(eq(schema.projects.userId, userId))
    .orderBy(desc(schema.projects.updatedAt))
    .limit(200);

  return Response.json({ projects: rows });
}

export async function POST(req: Request) {
  if (!db) return new Response("Persistence disabled", { status: 503 });
  let userId: string;
  try {
    userId = await requireUserId(req.headers);
  } catch (e) {
    if (e instanceof Unauthenticated) return new Response("Unauthorized", { status: 401 });
    throw e;
  }

  const body = (await req.json().catch(() => null)) as {
    name?: string;
    description?: string;
    instructions?: string;
  } | null;

  const name = body?.name?.trim();
  if (!name) return new Response("name required", { status: 400 });

  const [row] = await db
    .insert(schema.projects)
    .values({
      userId,
      name,
      description: body?.description?.trim() ?? "",
      instructions: body?.instructions?.trim() ?? "",
    })
    .returning();

  return Response.json({ project: row }, { status: 201 });
}
