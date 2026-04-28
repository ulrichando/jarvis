import { NextResponse } from "next/server";
import { execInRuntime, getRuntime, dockerStatus } from "@/lib/workspace/docker";

export const runtime = "nodejs";

// Returns the first port inside the workspace container that has
// something LISTENing on a TCP socket, mapped to its host port.
// Used by the chat-side runner: after a `start` action succeeds,
// the client polls this endpoint until it gets a hit, then offers
// a one-click preview.
//
// We run `ss -tlnp` inside the container (cheaper than a real HTTP
// probe and works for any framework). Filter to ports the container
// actually exposes (so we don't accidentally surface the bash
// listener for stdin or some internal tool).

export async function GET(_req: Request, ctx: RouteContext<"/api/workspace/[id]/preview">) {
  const { id } = await ctx.params;

  const status = await dockerStatus();
  if (!status.available || !status.imageReady) {
    return NextResponse.json({ port: null, hostPort: null, available: false });
  }

  const rt = await getRuntime(id);
  if (rt.state !== "running") {
    return NextResponse.json({ port: null, hostPort: null, available: false });
  }

  // Set of container ports we know we can reach from the host.
  const exposed = new Set(Object.keys(rt.ports).map(Number));

  // The container is debian-slim and doesn't ship `ss` or `netstat`,
  // so we parse /proc/net/tcp{,6} directly. Format: each row is
  // `sl local_address rem_address st ...` where local_address is
  // "AABBCCDD:PPPP" (hex) and st=0A means LISTEN.
  const r = await execInRuntime(
    id,
    "cat /proc/net/tcp /proc/net/tcp6 2>/dev/null || true",
    { timeoutMs: 4000 },
  );
  const stdout = r.stdout || "";

  const seen = new Set<number>();
  for (const line of stdout.split("\n")) {
    // sl local_address rem_address st tx rx ...
    const cols = line.trim().split(/\s+/);
    if (cols.length < 4) continue;
    if (cols[3] !== "0A") continue; // 0A = TCP_LISTEN
    const local = cols[1];
    const portHex = local.split(":").pop();
    if (!portHex) continue;
    const port = parseInt(portHex, 16);
    if (Number.isFinite(port) && port > 0) seen.add(port);
  }

  // Sandbox image only publishes 5173 to the host; the system prompt
  // teaches the model to bind there. Filter listening ports against
  // what's actually reachable.
  const candidates = [...seen].filter((p) => exposed.has(p));

  if (candidates.length === 0) {
    return NextResponse.json({ port: null, hostPort: null, available: true });
  }

  const port = candidates[0];
  const hostPort = rt.ports[String(port)] ?? null;

  return NextResponse.json({
    port,
    hostPort,
    available: true,
    listening: candidates,
    ports: rt.ports,
  });
}
