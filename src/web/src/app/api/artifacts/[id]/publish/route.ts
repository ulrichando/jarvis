import { db } from "@/lib/db";
import { getUserId } from "@/lib/auth-helpers";
import {
  setArtifactShareToken,
  clearArtifactShareToken,
} from "@/lib/artifacts/store";

export const runtime = "nodejs";

// POST → mint (or rotate) a public share token. Returns the public path.
export async function POST(
  req: Request,
  ctx: RouteContext<"/api/artifacts/[id]/publish">,
) {
  if (!db) return new Response("Persistence disabled", { status: 503 });
  const { id } = await ctx.params;
  const userId = await getUserId(req.headers);
  const minted = await setArtifactShareToken(id, userId);
  if (!minted) return new Response("Not found", { status: 404 });
  return Response.json({
    token: minted.token,
    expiresAt: minted.expiresAt,
    url: `/a/${minted.token}`,
  });
}

// DELETE → revoke the share token (un-publish).
export async function DELETE(
  req: Request,
  ctx: RouteContext<"/api/artifacts/[id]/publish">,
) {
  if (!db) return new Response("Persistence disabled", { status: 503 });
  const { id } = await ctx.params;
  const userId = await getUserId(req.headers);
  const ok = await clearArtifactShareToken(id, userId);
  return new Response(null, { status: ok ? 204 : 404 });
}
