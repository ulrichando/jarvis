import { NextResponse } from "next/server";
import { promises as fs } from "node:fs";
import path from "node:path";
import { build, type Plugin } from "esbuild";
import { resolveSafe } from "@/lib/workspace/storage";

export const runtime = "nodejs";

// Server-side bundler. Replaces the per-file path-mirror approach for
// design previews. Why: serving each .jsx separately + browser-native
// ESM is fragile (sandbox/CORS, base-href, MIME, JSX-runtime drift, React
// instance duplication). Bundling once with esbuild produces a single
// self-contained JS that the iframe loads via `<script src>`. No
// relative-import games, no React duplication, no JSX surprises.
//
// We bundle local files (App.jsx + components/) but mark `https://*`
// imports (esm.sh, etc.) as external — the browser still fetches React,
// motion, radix from the CDN, but with our import map deduping them to
// a single canonical version.
//
// Query: ?entry=<file> — relative path inside the workspace. We extract
// the FIRST inline `<script type="module">` from that file and use its
// content as the bundling entry, so model-written entries that look like
// `import App from "./App.jsx"; createRoot(...).render(<App/>)` just work.

function inMemoryEntryPlugin(workspaceRoot: string, entry: string): Plugin {
  return {
    name: "jarvis-workspace-resolve",
    setup(b) {
      // Root-relative imports for the curated shadcn bundle (and any
      // future static-public modules) stay external. The browser fetches
      // them directly from /public via the iframe's same-origin sandbox.
      b.onResolve({ filter: /^\/jarvis-/ }, (args) => ({
        path: args.path,
        external: true,
      }));
      // Relative imports (./ ../) resolve against the workspace.
      b.onResolve({ filter: /^\.\.?\// }, async (args) => {
        const baseDir = args.importer
          ? path.dirname(args.importer)
          : workspaceRoot;
        const resolved = path.resolve(baseDir, args.path);
        if (!resolved.startsWith(workspaceRoot)) {
          return { errors: [{ text: `Path ${args.path} escapes workspace` }] };
        }
        try {
          await fs.stat(resolved);
          return { path: resolved };
        } catch {
          // Try common extensions if missing — model sometimes writes
          // `import X from "./foo"` without `.jsx`.
          for (const ext of [".jsx", ".tsx", ".js", ".ts", ".mjs"]) {
            try {
              await fs.stat(resolved + ext);
              return { path: resolved + ext };
            } catch {}
          }
          return { errors: [{ text: `Cannot resolve ${args.path}` }] };
        }
      });
      // HTTPS imports (esm.sh, fonts, etc.) stay external. The browser
      // fetches them at runtime, deduped by the iframe's import map.
      b.onResolve({ filter: /^https?:\/\// }, (args) => ({
        path: args.path,
        external: true,
      }));
    },
  };
}

function extractInlineModuleScript(html: string): string | null {
  // Find the first <script type="module"> block, capture its body. We
  // skip <script type="importmap"> and src-only scripts.
  const re =
    /<script\b[^>]*\btype\s*=\s*["']module["'][^>]*>([\s\S]*?)<\/script>/i;
  const m = re.exec(html);
  if (!m) return null;
  // Don't bundle a script that has src=… (no inline content).
  const tag = m[0];
  if (/\bsrc\s*=/i.test(tag.split(">")[0])) return null;
  return m[1].trim();
}

export async function GET(
  req: Request,
  ctx: RouteContext<"/api/workspace/[id]/bundle">,
) {
  const { id } = await ctx.params;
  const url = new URL(req.url);
  const entry = url.searchParams.get("entry");
  if (!entry) {
    return NextResponse.json({ error: "missing entry" }, { status: 400 });
  }
  try {
    const entryAbs = resolveSafe(id, entry);
    const html = await fs.readFile(entryAbs, "utf8");
    const inline = extractInlineModuleScript(html);
    if (!inline) {
      return NextResponse.json(
        { error: `no <script type="module"> with inline code found in ${entry}` },
        { status: 400 },
      );
    }
    const workspaceRoot = path.dirname(entryAbs);

    const result = await build({
      stdin: {
        contents: inline,
        // resolveDir lets esbuild resolve `./App.jsx` from the inline
        // script as if it lived next to the entry HTML.
        resolveDir: workspaceRoot,
        loader: "jsx",
        sourcefile: "<entry-script>",
      },
      bundle: true,
      write: false,
      format: "esm",
      target: "es2022",
      jsx: "automatic",
      // jsxImportSource has to resolve to something the plugin returns as
      // external — `react` (bare) won't match the plugin's filters and
      // esbuild errors with "Could not resolve react/jsx-runtime". Using
      // the esm.sh URL makes esbuild emit `https://.../jsx-runtime` which
      // the plugin's HTTPS branch keeps external for the browser to fetch.
      jsxImportSource: "https://esm.sh/react@18.3.1",
      sourcemap: "inline",
      // Don't try to bundle node_modules — there are none in the design
      // workspace. Plugin handles all resolution.
      platform: "browser",
      logLevel: "silent",
      plugins: [inMemoryEntryPlugin(workspaceRoot, entryAbs)],
    });

    if (result.errors.length > 0) {
      return NextResponse.json(
        { error: result.errors.map((e) => e.text).join("\n") },
        { status: 500 },
      );
    }
    const file = result.outputFiles?.[0];
    if (!file) {
      return NextResponse.json({ error: "no output" }, { status: 500 });
    }
    return new Response(file.text, {
      headers: {
        "Content-Type": "application/javascript; charset=utf-8",
        "Cache-Control": "no-store",
      },
    });
  } catch (e) {
    const err = e as NodeJS.ErrnoException;
    // A missing entry file (ENOENT) is a client condition — a stale
    // workspace id, or files that were cleaned up — not a server fault.
    // Return 404 so the preview can render a graceful "no preview"
    // state instead of treating it as a 500 hard error.
    if (err?.code === "ENOENT") {
      return NextResponse.json(
        { error: `entry not found: ${entry}` },
        { status: 404 },
      );
    }
    return NextResponse.json(
      { error: err?.message ?? "bundle failed" },
      { status: 500 },
    );
  }
}
