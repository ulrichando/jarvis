import { NextResponse } from "next/server";
import { promises as fs } from "node:fs";
import path from "node:path";
import { getWorkspaceByShareToken, resolveSafe } from "@/lib/workspace/storage";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Public, read-only static asset server for share links. The share page
// (../page.tsx) points its iframe at /share/<token>/asset/<entry>.html so a
// static design renders WITH its relative assets (css, js, references/*.png).
//
// Security posture (this route is reachable WITHOUT a login — allowlisted by
// the /share prefix in src/proxy.ts):
//   • token-gated — only a workspace resolvable by a live, non-expired share
//     token is served; unknown/expired → 404 (can't probe for workspace ids).
//   • resolveSafe() rejects path traversal — every path stays INSIDE the ws.
//   • extension safelist — only render-safe static types (html/css/js/img/
//     font/json). No .env, no source maps, no arbitrary text. A shared design
//     is the rendered artifact, not a file browser.
const MAX_BYTES = 25 * 1024 * 1024;

const MIME: Record<string, string> = {
  html: "text/html; charset=utf-8",
  htm: "text/html; charset=utf-8",
  css: "text/css; charset=utf-8",
  js: "application/javascript; charset=utf-8",
  mjs: "application/javascript; charset=utf-8",
  json: "application/json; charset=utf-8",
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  webp: "image/webp",
  avif: "image/avif",
  svg: "image/svg+xml",
  ico: "image/x-icon",
  woff: "font/woff",
  woff2: "font/woff2",
  ttf: "font/ttf",
  otf: "font/otf",
};

export async function GET(
  _req: Request,
  ctx: RouteContext<"/share/[token]/asset/[...path]">,
) {
  const { token, path: segs } = await ctx.params;
  const ws = await getWorkspaceByShareToken(token);
  if (!ws) return new NextResponse("invalid or expired link", { status: 404 });

  const rel = (Array.isArray(segs) ? segs.join("/") : (segs ?? "")).replace(
    /^\/+/,
    "",
  );
  if (!rel) return new NextResponse("missing path", { status: 400 });

  const ext = path.extname(rel).slice(1).toLowerCase();
  const type = MIME[ext];
  if (!type) return new NextResponse("unsupported asset type", { status: 415 });

  try {
    const abs = resolveSafe(ws.id, rel); // throws if the path escapes the ws
    const st = await fs.stat(abs);
    if (!st.isFile()) return new NextResponse("not found", { status: 404 });
    if (st.size > MAX_BYTES) return new NextResponse("too large", { status: 413 });
    const buf = await fs.readFile(abs);
    const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
    return new Response(ab as ArrayBuffer, {
      headers: {
        "Content-Type": type,
        // Short cache so a re-share after an edit picks up fresh bytes.
        "Cache-Control": "public, max-age=30",
      },
    });
  } catch {
    return new NextResponse("not found", { status: 404 });
  }
}
