/**
 * GET /releases/<file> — serves prebuilt jarvis CLI binaries + the version
 * manifest for the /install.sh installer and the CLI's self-updater.
 *
 * Files live OUTSIDE the repo/build (a 100MB+ binary must never be committed
 * or bundled) in JARVIS_RELEASES_DIR — the deploy/build drops artifacts there:
 *   jarvis-linux-x64, jarvis-darwin-arm64, …  + manifest.json
 * Default ~/.jarvis/releases for local dev. Public (allowlisted in proxy.ts) —
 * the installer curls these with no session cookie.
 *
 * Hardened: only a strict `jarvis-<os>-<arch>` / `manifest.json` filename is
 * served, resolved + confined to the releases dir (no traversal), streamed
 * rather than buffered so a large binary doesn't pin the whole file in memory.
 */
import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import type { ReadableStream as NodeReadableStream } from "node:stream/web";
import { Readable } from "node:stream";

function releasesDir(): string {
  return process.env.JARVIS_RELEASES_DIR ?? join(homedir(), ".jarvis", "releases");
}

// Strict allowlist: a platform binary or the manifest. Anything else 404s
// before touching the filesystem — this is the path-traversal guard.
const ALLOWED = /^(jarvis-(linux|darwin)-(x64|arm64)|manifest\.json)$/;

type Ctx = { params: Promise<{ file: string }> };

export async function GET(_req: Request, ctx: Ctx): Promise<Response> {
  const { file } = await ctx.params;
  if (!ALLOWED.test(file)) {
    return new Response("not found", { status: 404 });
  }

  const dir = releasesDir();
  const full = resolve(dir, file);
  // Defense in depth: confirm the resolved path is still inside the dir.
  if (full !== join(dir, file)) {
    return new Response("not found", { status: 404 });
  }

  let size: number;
  try {
    const s = await stat(full);
    if (!s.isFile()) return new Response("not found", { status: 404 });
    size = s.size;
  } catch {
    return new Response("not published", { status: 404 });
  }

  const isManifest = file === "manifest.json";
  const webStream = Readable.toWeb(
    createReadStream(full),
  ) as unknown as NodeReadableStream<Uint8Array>;

  return new Response(webStream as unknown as BodyInit, {
    headers: {
      "content-type": isManifest
        ? "application/json; charset=utf-8"
        : "application/octet-stream",
      "content-length": String(size),
      ...(isManifest
        ? { "cache-control": "no-store" }
        : {
            "cache-control": "public, max-age=300",
            "content-disposition": `attachment; filename="${file}"`,
          }),
    },
  });
}
