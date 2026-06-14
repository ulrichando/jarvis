import { NextResponse } from "next/server";
import { promises as fs } from "node:fs";
import path from "node:path";
import { transform as esbuildTransform } from "esbuild";
import { resolveSafe } from "@/lib/workspace/storage";

export const runtime = "nodejs";

// Path-mirroring file server. The existing `/api/workspace/[id]/file?path=…`
// endpoint serves a single file by query param, but that URL shape breaks
// relative imports inside the iframe: a doc loaded at
// `/api/workspace/W/file?path=prototype.html` cannot resolve `./App.jsx`
// to anything reachable, because relative URLs drop the query string.
//
// This route mirrors the workspace's filesystem at a clean path namespace
// so `./App.jsx` from `prototype.html` resolves naturally to
// `/api/workspace/W/files/App.jsx` (and from there, `./components/Button.jsx`
// resolves to `/api/workspace/W/files/components/Button.jsx`, etc).
//
// JSX/TSX/MJS files are served as `application/javascript` so Babel
// standalone can fetch them as modules.

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

export async function GET(
  _req: Request,
  ctx: RouteContext<"/api/workspace/[id]/files/[...path]">,
) {
  const { id, path: parts } = await ctx.params;
  const segments = parts ?? [];
  const rel = segments.join("/");
  if (!rel) {
    return NextResponse.json({ error: "missing path" }, { status: 400 });
  }
  // Bound the path so a pathological request can't allocate a giant string
  // / deeply-nested walk. resolveSafe still enforces the no-escape rule.
  if (segments.length > 64 || rel.length > 1024) {
    return NextResponse.json({ error: "path too long" }, { status: 400 });
  }
  try {
    const abs = resolveSafe(id, rel);
    const ext = path.extname(rel).slice(1).toLowerCase();

    // JSX/TSX must be transformed before the browser can run them — native
    // browsers don't parse JSX. Babel-standalone in the iframe only handles
    // the inline <script>, NOT the imported sibling files. Without this
    // server-side transform the iframe loads ./App.jsx → browser parse
    // error → blank design (only the entry HTML's "frame" renders, every
    // imported component dies silently). We use esbuild's `jsx: "transform"`
    // mode which compiles <X/> to React.createElement(X), matching the
    // playbook's `import React from "https://esm.sh/react@18"`.
    if (ext === "jsx" || ext === "tsx" || ext === "ts") {
      const source = await fs.readFile(abs, "utf8");
      const loader = ext === "ts" ? "ts" : ext;
      const result = await esbuildTransform(source, {
        loader,
        // `automatic` mode auto-imports the JSX runtime — no need for the
        // model to remember `import React`. We point jsxImportSource at
        // esm.sh so the generated `import {jsx} from ".../jsx-runtime"`
        // resolves to a real URL the browser can fetch.
        jsx: "automatic",
        jsxImportSource: "https://esm.sh/react@18",
        target: "es2022",
        sourcemap: "inline",
        // Don't bundle — we want each imported file to remain its own
        // request so the iframe streams them lazily and our path-mirror
        // route can transform each on the fly.
      });
      return new Response(result.code, {
        headers: {
          "Content-Type": "application/javascript; charset=utf-8",
          "Cache-Control": "no-store",
        },
      });
    }

    const buf = await fs.readFile(abs);
    const type = MIME[ext] ?? "application/octet-stream";
    const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
    return new Response(ab as ArrayBuffer, {
      headers: {
        "Content-Type": type,
        // No-store: design files change on every generation; we don't want
        // the iframe to load a stale ./App.jsx after the model wrote a new one.
        "Cache-Control": "no-store",
      },
    });
  } catch (e) {
    return NextResponse.json(
      { error: (e as Error).message },
      { status: 404 },
    );
  }
}
