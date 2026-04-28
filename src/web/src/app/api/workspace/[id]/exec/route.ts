import { NextResponse } from "next/server";
import { execInRuntime, spawnDetached, dockerStatus } from "@/lib/workspace/docker";

export const runtime = "nodejs";
// Long installs can run for a few minutes — bump beyond Next's default.
export const maxDuration = 600;

export async function POST(req: Request, ctx: RouteContext<"/api/workspace/[id]/exec">) {
  const { id } = await ctx.params;
  const body = await req.json().catch(() => ({}));
  const command = typeof body?.command === "string" ? body.command : "";
  const detach = Boolean(body?.detach);
  if (!command) return NextResponse.json({ error: "missing command" }, { status: 400 });

  const status = await dockerStatus();
  if (!status.available || !status.imageReady) {
    return NextResponse.json(
      { error: "docker_not_ready", reason: !status.available ? "no_daemon" : "no_image" },
      { status: 503 },
    );
  }

  try {
    if (detach) {
      const r = await spawnDetached(id, command);
      return NextResponse.json({ ok: true, detached: true, ...r });
    }
    const r = await execInRuntime(id, command, { timeoutMs: 540_000 });
    return NextResponse.json({ ok: r.exitCode === 0, ...r });
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: 500 });
  }
}
