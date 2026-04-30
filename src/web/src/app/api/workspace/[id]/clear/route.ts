import { NextResponse, type NextRequest } from "next/server";
import { promises as fs } from "node:fs";
import path from "node:path";
import { workspaceRoot, touchWorkspace } from "@/lib/workspace/storage";

export const runtime = "nodejs";

// Clears every top-level entry in the workspace EXCEPT the `.jarvis/` dir,
// which holds settings (brand.json, brand assets) that should survive a wipe.
// Used when the user wants to regenerate from scratch — the prior design's
// HTML / components / references shouldn't pile up alongside the new ones.
export async function POST(
  _req: NextRequest,
  ctx: RouteContext<"/api/workspace/[id]/clear">,
) {
  const { id } = await ctx.params;
  const root = workspaceRoot(id);

  let entries: string[];
  try {
    entries = await fs.readdir(root);
  } catch (err) {
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "read failed" },
      { status: 400 },
    );
  }

  let cleared = 0;
  const errors: { name: string; error: string }[] = [];

  for (const name of entries) {
    if (name === ".jarvis") continue; // preserve workspace settings
    const abs = path.join(root, name);
    try {
      await fs.rm(abs, { recursive: true, force: true });
      cleared += 1;
    } catch (err) {
      errors.push({
        name,
        error: err instanceof Error ? err.message : String(err),
      });
    }
  }

  await touchWorkspace(id).catch(() => {});

  if (errors.length > 0) {
    return NextResponse.json(
      { ok: cleared > 0, cleared, errors },
      { status: 207 },
    );
  }
  return NextResponse.json({ ok: true, cleared });
}
