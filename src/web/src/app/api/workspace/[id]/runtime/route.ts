import { NextResponse } from "next/server";
import {
  dockerStatus,
  destroyRuntime,
  getRuntime,
  startRuntime,
  stopRuntime,
} from "@/lib/workspace/docker";
import { getWorkspace } from "@/lib/workspace/storage";

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
  // POST { action: "start" | "stop" | "restart" }
  // `restart` destroys the container then re-creates it — used by the
  // Settings UI when the user changes env vars (you can't update env
  // on a running container; recreation is the only safe path).
  const { id } = await ctx.params;
  const body = await req.json().catch(() => ({}));
  const action = body?.action;
  try {
    if (action === "start") {
      const ws = await getWorkspace(id);
      const rt = await startRuntime(id, ws?.envVars);
      return NextResponse.json({ mode: "docker", ...rt });
    }
    if (action === "stop") {
      await stopRuntime(id);
      const rt = await getRuntime(id);
      return NextResponse.json({ mode: "docker", ...rt });
    }
    if (action === "restart") {
      await destroyRuntime(id);
      const ws = await getWorkspace(id);
      const rt = await startRuntime(id, ws?.envVars);
      return NextResponse.json({ mode: "docker", ...rt });
    }
    return NextResponse.json({ error: "unknown action" }, { status: 400 });
  } catch (e) {
    return NextResponse.json({ error: (e as Error).message }, { status: 500 });
  }
}
