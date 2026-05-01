import { NextResponse } from "next/server";
import { promises as fs } from "node:fs";
import path from "node:path";
import {
  readFile,
  writeFile,
  deleteEntry,
  createEntry,
  resolveSafe,
} from "@/lib/workspace/storage";

export const runtime = "nodejs";

const MIME: Record<string, string> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  webp: "image/webp",
  avif: "image/avif",
  svg: "image/svg+xml",
  html: "text/html; charset=utf-8",
  htm: "text/html; charset=utf-8",
  css: "text/css; charset=utf-8",
  js: "application/javascript; charset=utf-8",
  mjs: "application/javascript; charset=utf-8",
  jsx: "application/javascript; charset=utf-8",
  ts: "application/javascript; charset=utf-8",
  tsx: "application/javascript; charset=utf-8",
  pdf: "application/pdf",
  json: "application/json; charset=utf-8",
  txt: "text/plain; charset=utf-8",
  md: "text/markdown; charset=utf-8",
};

export async function GET(req: Request, ctx: RouteContext<"/api/workspace/[id]/file">) {
  const { id } = await ctx.params;
  const url = new URL(req.url);
  const rel = url.searchParams.get("path");
  const raw = url.searchParams.get("raw");
  if (!rel) return NextResponse.json({ error: "missing path" }, { status: 400 });

  // Raw mode: stream the file body so previews (images, PDFs) and iframe
  // src= work without base64ing through JSON.
  if (raw) {
    try {
      const abs = resolveSafe(id, rel);
      const buf = await fs.readFile(abs);
      const ext = path.extname(rel).slice(1).toLowerCase();
      const type = MIME[ext] ?? "application/octet-stream";
      const ab = buf.buffer.slice(
        buf.byteOffset,
        buf.byteOffset + buf.byteLength,
      );
      return new Response(ab as ArrayBuffer, {
        headers: { "Content-Type": type, "Cache-Control": "no-store" },
      });
    } catch (e) {
      return NextResponse.json({ error: (e as Error).message }, { status: 400 });
    }
  }

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
