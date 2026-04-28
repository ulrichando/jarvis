import { NextResponse } from "next/server";
import { readFile, writeFile, deleteEntry, createEntry } from "@/lib/workspace/storage";

export const runtime = "nodejs";

export async function GET(req: Request, ctx: RouteContext<"/api/workspace/[id]/file">) {
  const { id } = await ctx.params;
  const url = new URL(req.url);
  const rel = url.searchParams.get("path");
  if (!rel) return NextResponse.json({ error: "missing path" }, { status: 400 });
  try {
    const content = await readFile(id, rel);
    return NextResponse.json({ content });
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: 400 });
  }
}

export async function PUT(req: Request, ctx: RouteContext<"/api/workspace/[id]/file">) {
  const { id } = await ctx.params;
  const body = await req.json().catch(() => ({}));
  const { path: rel, content } = body as { path?: string; content?: string };
  if (!rel || typeof content !== "string")
    return NextResponse.json({ error: "missing path or content" }, { status: 400 });
  try {
    await writeFile(id, rel, content);
    return NextResponse.json({ ok: true });
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: 400 });
  }
}

export async function POST(req: Request, ctx: RouteContext<"/api/workspace/[id]/file">) {
  // create a new file or directory
  const { id } = await ctx.params;
  const body = await req.json().catch(() => ({}));
  const { path: rel, type } = body as { path?: string; type?: "file" | "dir" };
  if (!rel || (type !== "file" && type !== "dir"))
    return NextResponse.json({ error: "missing path or type" }, { status: 400 });
  try {
    await createEntry(id, rel, type);
    return NextResponse.json({ ok: true });
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: 400 });
  }
}

export async function DELETE(req: Request, ctx: RouteContext<"/api/workspace/[id]/file">) {
  const { id } = await ctx.params;
  const url = new URL(req.url);
  const rel = url.searchParams.get("path");
  if (!rel) return NextResponse.json({ error: "missing path" }, { status: 400 });
  try {
    await deleteEntry(id, rel);
    return NextResponse.json({ ok: true });
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: 400 });
  }
}
