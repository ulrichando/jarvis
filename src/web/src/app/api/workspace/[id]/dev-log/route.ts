import { NextResponse } from "next/server";
import { promises as fs } from "node:fs";
import { resolveSafe } from "@/lib/workspace/storage";

export const runtime = "nodejs";

/**
 * GET /api/workspace/[id]/dev-log
 *
 * Returns the captured stdout+stderr of the most recently started dev
 * server (anything spawned via `<boltAction type="start">`). The log
 * lives at `.jarvis/dev.log` inside the workspace's bind-mount —
 * `spawnDetached` truncates + redirects on every start, so this is
 * always the *current* run's output.
 *
 * Query params:
 *   - `bytes` (default 32768): cap response size. Tail of the file so
 *     the freshest output is always included, with head dropped on
 *     overflow.
 *
 * Used by:
 *   - The model, via `<boltAction type="shell">tail -200 .jarvis/dev.log</boltAction>`
 *     — that path goes through exec, not this endpoint.
 *   - The future "Problems" panel UI to surface errors to the user
 *     without making them open a terminal.
 */
export async function GET(
  req: Request,
  ctx: RouteContext<"/api/workspace/[id]/dev-log">,
) {
  const { id } = await ctx.params;
  const url = new URL(req.url);
  const cap = Math.min(
    Math.max(Number(url.searchParams.get("bytes") ?? 32_768), 1024),
    1_048_576,
  );

  const logPath = resolveSafe(id, ".jarvis/dev.log");
  let content = "";
  let exists = false;
  let size = 0;

  try {
    const stat = await fs.stat(logPath);
    exists = true;
    size = stat.size;
    if (size <= cap) {
      content = await fs.readFile(logPath, "utf8");
    } else {
      // Tail: open + position read so we don't pull the whole file into
      // memory for a giant log.
      const fh = await fs.open(logPath, "r");
      try {
        const buf = Buffer.alloc(cap);
        await fh.read(buf, 0, cap, size - cap);
        // The slice may start mid-line — drop everything up to and
        // including the first newline so the response is line-aligned.
        const decoded = buf.toString("utf8");
        const nl = decoded.indexOf("\n");
        content =
          nl >= 0 && nl < decoded.length - 1
            ? decoded.slice(nl + 1)
            : decoded;
      } finally {
        await fh.close();
      }
    }
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code !== "ENOENT") {
      return NextResponse.json(
        { error: (err as Error).message },
        { status: 500 },
      );
    }
  }

  return NextResponse.json({
    exists,
    size,
    truncated: exists && size > cap,
    content,
  });
}
