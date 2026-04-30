import { NextResponse } from "next/server";
import { getWorkspace, deleteWorkspace, renameWorkspace } from "@/lib/workspace/storage";
import { destroyRuntime, dockerStatus } from "@/lib/workspace/docker";

export const runtime = "nodejs";

export async function GET(_req: Request, ctx: RouteContext<"/api/workspace/[id]">) {
  const { id } = await ctx.params;
  const ws = await getWorkspace(id);
  if (!ws) return NextResponse.json({ error: "not_found" }, { status: 404 });
  return NextResponse.json({ workspace: ws });
}

export async function PATCH(req: Request, ctx: RouteContext<"/api/workspace/[id]">) {
  const { id } = await ctx.params;
  const body = (await req.json().catch(() => ({}))) as { name?: unknown };
  const name = typeof body.name === "string" ? body.name : "";
  if (!name.trim() || name.length > 80) {
    return NextResponse.json({ error: "invalid name" }, { status: 400 });
  }
  const ws = await renameWorkspace(id, name);
  if (!ws) return NextResponse.json({ error: "not_found" }, { status: 404 });
  return NextResponse.json({ workspace: ws });
}

export async function DELETE(_req: Request, ctx: RouteContext<"/api/workspace/[id]">) {
  const { id } = await ctx.params;
  // Tear down the sandbox container first so we don't orphan it. Failures
  // here are non-fatal — if docker isn't running, we still want to nuke
  // the on-disk workspace.
  try {
    const s = await dockerStatus();
    if (s.available) await destroyRuntime(id);
  } catch {}
  await deleteWorkspace(id);
  return NextResponse.json({ ok: true });
}
