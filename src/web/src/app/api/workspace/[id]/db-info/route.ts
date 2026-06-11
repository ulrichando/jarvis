import { NextResponse } from "next/server";
import { promises as fs } from "node:fs";
import path from "node:path";
import { workspaceRoot } from "@/lib/workspace/storage";
import { execInRuntime } from "@/lib/workspace/docker";

export const runtime = "nodejs";

// Inspect the workspace's SQLite database. Returns size + schema for
// the Settings UI's Database section. Looks for the conventional
// `data/app.db` path (what the build seed mandates) but walks the
// `data/` directory for any *.db / *.sqlite / *.sqlite3 files so
// non-standard locations still surface.
//
// Schema query runs INSIDE the sandbox container (not on the host)
// because (a) the host may not have sqlite3 CLI, (b) the host doesn't
// see /workspace mounts the same way the container does, and
// (c) keeps a single source of truth for "where the runtime sees the db".
export async function GET(
  _req: Request,
  ctx: { params: Promise<{ id: string }> },
) {
  const { id } = await ctx.params;

  // Find db files on the host filesystem first — that's authoritative
  // for "is there a db at all", since the bind-mounted /workspace has
  // the same files visible from both sides.
  const root = workspaceRoot(id);
  const dataDir = path.join(root, "data");
  let files: { name: string; bytes: number }[] = [];
  try {
    const entries = await fs.readdir(dataDir, { withFileTypes: true });
    for (const e of entries) {
      if (!e.isFile()) continue;
      if (!/\.(db|sqlite|sqlite3)$/.test(e.name)) continue;
      const stat = await fs.stat(path.join(dataDir, e.name));
      files.push({ name: e.name, bytes: stat.size });
    }
  } catch {
    // No data/ directory — workspace just doesn't have a DB yet.
    files = [];
  }

  if (files.length === 0) {
    return NextResponse.json({ exists: false, files: [], tables: [] });
  }

  // Pick the largest db file as the "primary" one. Most projects only
  // have one; if there are multiple, this is a reasonable default
  // until we surface a per-file picker.
  files.sort((a, b) => b.bytes - a.bytes);
  const primary = files[0];

  // Pull schema via the sandbox's sqlite3 CLI. Tab-separated output
  // makes parsing straightforward. `.tables` lists names; for each we
  // run `SELECT count(*)` to surface row counts.
  const tables: { name: string; rows: number }[] = [];
  try {
    const r = await execInRuntime(
      id,
      `sqlite3 "/workspace/data/${primary.name}" ".tables"`,
      { timeoutMs: 5000 },
    );
    const tableNames = r.stdout
      .split(/\s+/)
      .map((s) => s.trim())
      .filter((s) => s.length > 0 && !s.startsWith("sqlite_"));

    // Best-effort row counts. Bail on the first error so we don't
    // hammer a broken db.
    for (const name of tableNames) {
      try {
        // Quote the table name to handle SQL keywords used as names.
        const cmd = `sqlite3 "/workspace/data/${primary.name}" 'SELECT count(*) FROM "${name.replace(/"/g, '""')}";'`;
        const cr = await execInRuntime(id, cmd, { timeoutMs: 3000 });
        const rows = parseInt(cr.stdout.trim(), 10);
        tables.push({
          name,
          rows: Number.isFinite(rows) ? rows : 0,
        });
      } catch {
        tables.push({ name, rows: 0 });
      }
    }
  } catch (err) {
    // sqlite3 not available in the sandbox or container not running.
    // Surface as ok=true with empty tables so the UI can show "DB
    // exists, schema unavailable" instead of erroring out.
    return NextResponse.json({
      exists: true,
      files,
      tables: [],
      schemaError:
        err instanceof Error ? err.message : "schema query failed",
    });
  }

  return NextResponse.json({ exists: true, files, tables });
}
