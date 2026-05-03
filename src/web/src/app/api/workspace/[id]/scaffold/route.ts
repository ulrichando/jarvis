import { NextResponse } from "next/server";
import { SCAFFOLDS, applyScaffold, findScaffold } from "@/lib/scaffolds";
import { listAllFiles } from "@/lib/workspace/storage";
import {
  dockerStatus,
  execInRuntime,
  spawnDetached,
} from "@/lib/workspace/docker";

export const runtime = "nodejs";
export const maxDuration = 600;

/**
 * GET /api/workspace/[id]/scaffold
 *   → { scaffolds: Scaffold[], hasFiles: boolean }
 *
 * Lists the scaffolds the user can pick from. `hasFiles` lets the UI
 * decide whether to show the picker (empty workspace) or not.
 *
 * POST /api/workspace/[id]/scaffold
 *   body: { scaffoldId: string, install?: boolean, start?: boolean }
 *   → { copied[], skipped[], installed?, started? }
 *
 * Copies the scaffold's files into the workspace (skipping anything
 * that already exists) and optionally runs `bun install` and the
 * dev server. Default: install=true, start=true so the user gets a
 * green workspace in one click.
 */
export async function GET(
  _req: Request,
  ctx: RouteContext<"/api/workspace/[id]/scaffold">,
) {
  const { id } = await ctx.params;
  const files = await listAllFiles(id);
  return NextResponse.json({
    scaffolds: SCAFFOLDS,
    hasFiles: files.length > 0,
  });
}

export async function POST(
  req: Request,
  ctx: RouteContext<"/api/workspace/[id]/scaffold">,
) {
  const { id } = await ctx.params;
  const body = await req.json().catch(() => ({}));
  const scaffoldId = String(body.scaffoldId ?? "");
  const install = body.install !== false;
  const start = body.start !== false;

  const scaffold = findScaffold(scaffoldId);
  if (!scaffold) {
    return NextResponse.json(
      { error: "unknown_scaffold", scaffoldId },
      { status: 400 },
    );
  }

  // Copy files first — cheap, host-only.
  const { copied, skipped } = await applyScaffold({
    workspaceId: id,
    scaffoldId,
  });

  // Install + start are both gated on the docker workbench being up.
  // If it's not, return what we copied and let the UI surface the
  // missing-docker case (the regular preview/runtime polling will
  // handle the rest).
  let installed = false;
  let started = false;
  const status = await dockerStatus();
  if (install && status.available && status.imageReady) {
    try {
      const r = await execInRuntime(
        id,
        "cd /workspace && bun install",
        { timeoutMs: 240_000 },
      );
      installed = r.exitCode === 0;
    } catch {
      /* let the user retry via /preview/autostart */
    }
  }
  if (start && status.available && status.imageReady) {
    try {
      await spawnDetached(id, "cd /workspace && bun run dev");
      started = true;
    } catch {
      /* same */
    }
  }

  return NextResponse.json({
    scaffold: scaffold.id,
    label: scaffold.label,
    hints: scaffold.hints,
    copied,
    skipped,
    installed,
    started,
  });
}
