import { db } from "@/lib/db";
import { requireUserId, Unauthenticated } from "@/lib/auth-helpers";
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
  let userId: string;
  try {
    userId = await requireUserId(req.headers);
  } catch (e) {
    if (e instanceof Unauthenticated) return new Response("Unauthorized", { status: 401 });
    throw e;
  }
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
  let userId: string;
  try {
    userId = await requireUserId(req.headers);
  } catch (e) {
    if (e instanceof Unauthenticated) return new Response("Unauthorized", { status: 401 });
    throw e;
  }
  const ok = await clearArtifactShareToken(id, userId);
  return new Response(null, { status: ok ? 204 : 404 });
}
