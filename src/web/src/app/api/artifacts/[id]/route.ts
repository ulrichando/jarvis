import { db } from "@/lib/db";
import { getUserId } from "@/lib/auth-helpers";
import { getArtifact, renameArtifact, deleteArtifact } from "@/lib/artifacts/store";

export const runtime = "nodejs";

export async function GET(
  req: Request,
  ctx: RouteContext<"/api/artifacts/[id]">,
) {
  if (!db) return new Response("Persistence disabled", { status: 503 });
  const { id } = await ctx.params;
  const userId = await getUserId(req.headers);
  const artifact = await getArtifact(id, userId);
  if (!artifact) return new Response("Not found", { status: 404 });
  return Response.json({ artifact });
}

export async function PATCH(
  req: Request,
  ctx: RouteContext<"/api/artifacts/[id]">,
) {
  if (!db) return new Response("Persistence disabled", { status: 503 });
  const { id } = await ctx.params;
  const userId = await getUserId(req.headers);
  const body = (await req.json().catch(() => ({}))) as { title?: unknown };
  if (typeof body.title !== "string" || !body.title.trim()) {
    return new Response("title required", { status: 400 });
  }
  const ok = await renameArtifact(id, userId, body.title.trim());
  if (!ok) return new Response("Not found", { status: 404 });
  return Response.json({ ok: true });
}

export async function DELETE(
  req: Request,
  ctx: RouteContext<"/api/artifacts/[id]">,
) {
  if (!db) return new Response("Persistence disabled", { status: 503 });
  const { id } = await ctx.params;
  const userId = await getUserId(req.headers);
  const ok = await deleteArtifact(id, userId);
  return new Response(null, { status: ok ? 204 : 404 });
}
