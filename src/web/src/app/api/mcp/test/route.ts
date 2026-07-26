import { NextResponse } from "next/server";
import { testMcpServer } from "@/lib/mcp/client";
import { listMcpServers } from "@/lib/mcp/store";
import { requireMcpAuth } from "@/lib/mcp/authz";

// POST /api/mcp/test
//   { id }                        → test an already-configured server, using its
//                                    stored headers (so the browser never needs
//                                    the token back).
//   { url, transport?, headers? } → validate a candidate before adding it.
//
// Auth-gated: the { url, headers } shape makes the SERVER open a connection to
// an arbitrary caller-supplied URL with caller-supplied headers (SSRF vector),
// and the { id } shape replays a stored server's REAL auth headers outbound.
// proxy.ts's bearer gate is opt-in, so gate in-handler like the /api/mcp
// mutations do.
export async function POST(req: Request): Promise<NextResponse> {
  const denied = await requireMcpAuth(req);
  if (denied) return denied;
  const body = (await req.json().catch(() => ({}))) as {
    id?: string;
    name?: string;
    url?: string;
    transport?: string;
    headers?: Record<string, string>;
  };

  if (body.id) {
    const server = (await listMcpServers()).find((s) => s.id === body.id);
    if (!server) {
      return NextResponse.json({ ok: false, error: "server not found" }, { status: 404 });
    }
    if (!server.url) {
      return NextResponse.json(
        { ok: false, error: "stdio servers can't be tested over HTTP" },
        { status: 400 },
      );
    }
    return NextResponse.json(await testMcpServer(server));
  }

  if (!body.url) {
    return NextResponse.json({ ok: false, error: "url required" }, { status: 400 });
  }
  try {
    new URL(body.url);
  } catch {
    return NextResponse.json({ ok: false, error: "invalid url" }, { status: 400 });
  }
  const r = await testMcpServer({
    id: "test",
    name: body.name ?? "test",
    url: body.url,
    transport: body.transport === "sse" ? "sse" : "http",
    headers: body.headers,
    enabled: true,
  });
  return NextResponse.json(r);
}
