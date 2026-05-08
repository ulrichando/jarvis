import { NextResponse } from "next/server";
import { gitStatus } from "@/lib/workspace/git";

export const runtime = "nodejs";

export async function GET(
  _req: Request,
  ctx: { params: Promise<{ id: string }> },
) {
  const { id } = await ctx.params;
  const status = await gitStatus(id);
  return NextResponse.json(status);
}
