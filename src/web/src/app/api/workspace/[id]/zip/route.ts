import { promises as fs } from "node:fs";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { workspaceRoot, getWorkspace } from "@/lib/workspace/storage";

const execFileP = promisify(execFile);

export const runtime = "nodejs";
export const maxDuration = 120;

/**
 * GET /api/workspace/[id]/zip
 *
 * Streams the workspace's source files as a single ZIP archive. Skips
 * node_modules, .next, .git, .jarvis, dist/build, and the SQLite db
 * files (those usually aren't worth carrying — the user can rebuild).
 *
 * Implementation: shells out to `zip` on the HOST (where the
 * workspace's bind-mount lives). Avoids round-tripping through docker
 * exec because the container image doesn't ship `zip` by default,
 * while the host is guaranteed to have it. The archive is built into
 * /tmp and streamed back, then cleaned up.
 *
 * This is what every production AI coding tool ships as the bare
 * minimum "take your work home" feature: Bolt, Lovable, v0, Replit
 * all expose it.
 */
export async function GET(
  _req: Request,
  ctx: RouteContext<"/api/workspace/[id]/zip">,
) {
  const { id } = await ctx.params;
  const ws = await getWorkspace(id);
  if (!ws) return new Response("workspace not found", { status: 404 });

  const cwd = workspaceRoot(id);
  // Stage the zip in /tmp with a unique name so concurrent exports
  // don't race. Cleaned up after the response is built.
  const tmpZip = `/tmp/jarvis-export-${id}-${Date.now()}.zip`;

  // -r recursive, -q quiet, -x exclude patterns matched against the
  // archive-internal path. We run from `cwd` so paths in the archive
  // are relative — opening the zip extracts straight into a project
  // dir without a deep nested folder.
  try {
    await execFileP(
      "zip",
      [
        "-r",
        "-q",
        tmpZip,
        ".",
        "-x",
        "node_modules/*",
        "-x",
        ".next/*",
        "-x",
        ".git/*",
        "-x",
        "dist/*",
        "-x",
        "build/*",
        "-x",
        ".jarvis/*",
        "-x",
        ".turbo/*",
        "-x",
        ".cache/*",
        "-x",
        ".pnpm-store/*",
        "-x",
        ".yarn/cache/*",
        "-x",
        "data/*.db",
        "-x",
        "data/*.db-*",
      ],
      { cwd, timeout: 90_000, maxBuffer: 16 * 1024 * 1024 },
    );
  } catch (err) {
    return new Response(
      `zip failed: ${(err as Error).message}`,
      { status: 500 },
    );
  }

  let buf: Buffer;
  try {
    buf = await fs.readFile(tmpZip);
  } catch (err) {
    return new Response(
      `failed to read zip: ${(err as Error).message}`,
      { status: 500 },
    );
  }

  // Best-effort cleanup so /tmp doesn't accumulate stale exports.
  fs.rm(tmpZip, { force: true }).catch(() => {});

  // Slug the workspace name for the download filename.
  const safeName =
    (ws.name ?? "workspace")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 40) || "workspace";
  const stamp = new Date().toISOString().slice(0, 10);
  const filename = `${safeName}-${stamp}.zip`;

  const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
  return new Response(ab as ArrayBuffer, {
    headers: {
      "Content-Type": "application/zip",
      "Content-Disposition": `attachment; filename="${filename}"`,
      "Content-Length": String(buf.byteLength),
      "Cache-Control": "no-store",
    },
  });
}
