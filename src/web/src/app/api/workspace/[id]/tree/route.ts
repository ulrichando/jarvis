import { NextResponse } from "next/server";
import { readTree } from "@/lib/workspace/storage";

export const runtime = "nodejs";

export async function GET(req: Request, ctx: RouteContext<"/api/workspace/[id]/tree">) {
  const { id } = await ctx.params;
  const url = new URL(req.url);
  const rel = url.searchParams.get("path") ?? "";
  try {
    const entries = await readTree(id, rel);
    return NextResponse.json({ entries });
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: 400 });
  }
}
