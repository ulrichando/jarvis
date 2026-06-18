import { NextResponse } from "next/server";
import { testMcpServer } from "@/lib/mcp/client";
import { listMcpServers } from "@/lib/mcp/store";

// POST /api/mcp/test
//   { id }                        → test an already-configured server, using its
//                                    stored headers (so the browser never needs
//                                    the token back).
//   { url, transport?, headers? } → validate a candidate before adding it.
export async function POST(req: Request): Promise<NextResponse> {
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
