import { NextResponse } from "next/server";
import { gitRestore } from "@/lib/workspace/git";

export const runtime = "nodejs";
export const maxDuration = 60;

export async function POST(
  req: Request,
  ctx: RouteContext<"/api/workspace/[id]/commit/restore">,
) {
  const { id } = await ctx.params;
  const body = await req.json().catch(() => ({}));
  const sha = String(body.sha ?? "").trim();
  if (!sha) {
    return NextResponse.json({ error: "missing sha" }, { status: 400 });
  }
  try {
    await gitRestore(id, sha);
    return NextResponse.json({ ok: true });
  } catch (err) {
    console.error("[commit/restore] failed:", err);
    return NextResponse.json(
      { error: "restore_failed", message: String(err) },
      { status: 500 },
    );
  }
}
