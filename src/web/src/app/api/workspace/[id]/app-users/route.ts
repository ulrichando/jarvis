import { NextResponse } from "next/server";
import { execInRuntime } from "@/lib/workspace/docker";

export const runtime = "nodejs";

/**
 * App-user management — operates on the deployed app's `users` table
 * inside the workspace's SQLite db. NOT to be confused with workbench
 * itself's authentication.
 *
 * GET    /api/workspace/[id]/app-users
 *        → { configured, users: [{ id, email, name, role, created_at }], rowCount }
 *        Auto-detects schema: looks for a `users` table; if columns
 *        diverge from the convention, returns the rows the convention
 *        understands (id, email, name, role, created_at).
 *
 * DELETE /api/workspace/[id]/app-users?id=...
 *        Soft-fails if the table has no `id` column.
 *
 * Schema-detection is best-effort. Real production user-mgmt would
 * have invite, password reset, role assignment — those depend on the
 * deployed app's own auth system. We surface what we can read and
 * provide a deletion handle.
 */

const PRIMARY_DB = "/workspace/data/app.db";
const FIELD_LIST = ["id", "email", "name", "role", "created_at"] as const;

async function tableExists(workspaceId: string): Promise<boolean> {
  try {
    const r = await execInRuntime(
      workspaceId,
      `sqlite3 ${PRIMARY_DB} "SELECT name FROM sqlite_master WHERE type='table' AND name='users';" 2>/dev/null`,
      { timeoutMs: 5000 },
    );
    return r.stdout.trim() === "users";
  } catch {
    return false;
  }
}

async function tableColumns(workspaceId: string): Promise<string[]> {
  try {
    const r = await execInRuntime(
      workspaceId,
      `sqlite3 ${PRIMARY_DB} "PRAGMA table_info(users);" 2>/dev/null`,
      { timeoutMs: 5000 },
    );
    return r.stdout
      .split("\n")
      .map((l) => l.split("|")[1])
      .filter(Boolean);
  } catch {
    return [];
  }
}

export async function GET(
  _req: Request,
  ctx: { params: Promise<{ id: string }> },
) {
  const { id } = await ctx.params;
  if (!(await tableExists(id))) {
    return NextResponse.json({
      configured: false,
      users: [],
      rowCount: 0,
      hint:
        "No `users` table in /workspace/data/app.db yet. Scaffold Auth (Settings → Authentication) to create one, or have the AI add one in chat.",
    });
  }
  const cols = await tableColumns(id);
  // Pick the columns the table has from our preferred list; preserve
  // ordering for the UI.
  const selected = FIELD_LIST.filter((c) => cols.includes(c));
  if (selected.length === 0) {
    return NextResponse.json({
      configured: true,
      users: [],
      rowCount: 0,
      hint: "Found a `users` table but none of the expected columns (id, email, name, role, created_at).",
    });
  }
  // Limit query to 100 rows to keep the UI fast. Pagination is V2.
  const select = selected.join(", ");
  let rows: Record<string, unknown>[] = [];
  let rowCount = 0;
  try {
    const r = await execInRuntime(
      id,
      `sqlite3 -separator '\\t' ${PRIMARY_DB} "SELECT ${select} FROM users ORDER BY rowid DESC LIMIT 100;" 2>/dev/null`,
      { timeoutMs: 8000 },
    );
    rows = r.stdout
      .split("\n")
      .filter((l) => l.length > 0)
      .map((l) => {
        const vals = l.split("\t");
        const o: Record<string, unknown> = {};
        selected.forEach((c, i) => {
          o[c] = vals[i] ?? null;
        });
        return o;
      });
    const c = await execInRuntime(
      id,
      `sqlite3 ${PRIMARY_DB} "SELECT count(*) FROM users;" 2>/dev/null`,
      { timeoutMs: 5000 },
    );
    rowCount = parseInt(c.stdout.trim(), 10) || 0;
  } catch (err) {
    return NextResponse.json(
      {
        configured: true,
        users: [],
        rowCount: 0,
        error: err instanceof Error ? err.message : String(err),
      },
      { status: 500 },
    );
  }
  return NextResponse.json({
    configured: true,
    users: rows,
    rowCount,
    columns: selected,
  });
}

export async function DELETE(
  req: Request,
  ctx: { params: Promise<{ id: string }> },
) {
  const { id } = await ctx.params;
  const url = new URL(req.url);
  const userId = (url.searchParams.get("id") ?? "").trim();
  if (!userId) return NextResponse.json({ error: "missing_id" }, { status: 400 });
  // Hard-validate the id charset BEFORE it touches a shell. The delete
  // runs as `sqlite3 ... "DELETE ... '<id>';"` INSIDE the container, so an
  // id containing a double-quote / $ / backtick would break out of the
  // shell double-quotes (command injection in the sandbox), and a single
  // quote would break the SQL. App-user ids are integers / uuids / cuids
  // in practice — allow only that safe charset and reject anything else.
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(userId)) {
    return NextResponse.json({ error: "invalid_id" }, { status: 400 });
  }
  if (!(await tableExists(id))) {
    return NextResponse.json(
      { error: "no_users_table" },
      { status: 400 },
    );
  }
  try {
    await execInRuntime(
      id,
      `sqlite3 ${PRIMARY_DB} "DELETE FROM users WHERE id = '${userId}';"`,
      { timeoutMs: 5000 },
    );
    return NextResponse.json({ ok: true });
  } catch (err) {
    return NextResponse.json(
      {
        error: "delete_failed",
        message: err instanceof Error ? err.message : String(err),
      },
      { status: 500 },
    );
  }
}
