import { NextResponse } from "next/server";
import {
  dockerStatus,
  getRuntime,
  startRuntime,
  stopRuntime,
} from "@/lib/workspace/docker";

export const runtime = "nodejs";

export async function GET(_req: Request, ctx: RouteContext<"/api/workspace/[id]/runtime">) {
  const { id } = await ctx.params;
  const status = await dockerStatus();
  if (!status.available) {
    return NextResponse.json({
      mode: "local",
      reason: "docker_unavailable",
      state: "absent",
      ports: {},
    });
  }
  if (!status.imageReady) {
    return NextResponse.json({
      mode: "local",
      reason: "image_missing",
      state: "absent",
      ports: {},
    });
  }
  const rt = await getRuntime(id);
  return NextResponse.json({ mode: "docker", ...rt });
}

export async function POST(req: Request, ctx: RouteContext<"/api/workspace/[id]/runtime">) {
  // POST { action: "start" | "stop" }
  const { id } = await ctx.params;
  const body = await req.json().catch(() => ({}));
  const action = body?.action;
  try {
    if (action === "start") {
      const rt = await startRuntime(id);
      return NextResponse.json({ mode: "docker", ...rt });
    }
    if (action === "stop") {
      await stopRuntime(id);
      const rt = await getRuntime(id);
      return NextResponse.json({ mode: "docker", ...rt });
    }
    return NextResponse.json({ error: "unknown action" }, { status: 400 });
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: 500 });
  }
}
