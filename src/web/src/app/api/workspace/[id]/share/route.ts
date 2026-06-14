import { NextResponse } from "next/server";
import { setShareToken, clearShareToken } from "@/lib/workspace/storage";

export const runtime = "nodejs";

/**
 * POST   /api/workspace/[id]/share  → mint (or refresh) a read-only share
 *                                     link. Returns { token, path, expiresAt }.
 * DELETE /api/workspace/[id]/share  → revoke the share link.
 *
 * The link points at the public /share/<token> page, which renders ONLY the
 * deployed site (if any) — never source files or secrets. Gated by the
 * normal proxy auth: only the owner can mint/revoke; the resulting page is
 * public-by-token.
 */
export async function POST(
  _req: Request,
  ctx: { params: Promise<{ id: string }> },
) {
  const { id } = await ctx.params;
  const share = await setShareToken(id);
  if (!share) {
    return NextResponse.json({ error: "not_found" }, { status: 404 });
  }
  return NextResponse.json({
    token: share.token,
    path: `/share/${share.token}`,
    expiresAt: share.expiresAt,
  });
}

export async function DELETE(
  _req: Request,
  ctx: { params: Promise<{ id: string }> },
) {
  const { id } = await ctx.params;
  await clearShareToken(id);
  return NextResponse.json({ ok: true });
}
