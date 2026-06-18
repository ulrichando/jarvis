import { NextResponse } from "next/server";
import {
  listMcpServers,
  addMcpServer,
  removeMcpServer,
  setMcpServerEnabled,
} from "@/lib/mcp/store";

// GET /api/mcp — JARVIS's MCP servers (from ~/.jarvis/mcp.json).
// Auth headers are REDACTED here: the browser only learns whether a server has
// auth (hasAuth), never the token itself. Server-side consumers (chat, test)
// read the real headers straight from the store.
export async function GET(): Promise<NextResponse> {
  const servers = (await listMcpServers()).map(({ headers, ...s }) => ({
    ...s,
    hasAuth: !!headers && Object.keys(headers).length > 0,
  }));
  return NextResponse.json({ servers });
}

// POST /api/mcp { name, url, transport?, headers? } — add an HTTP/SSE server.
export async function POST(req: Request): Promise<NextResponse> {
  const body = (await req.json().catch(() => ({}))) as {
    name?: string;
    url?: string;
    transport?: string;
    headers?: Record<string, string>;
  };
  if (!body.name?.trim() || !body.url?.trim()) {
    return NextResponse.json({ error: "name and url required" }, { status: 400 });
  }
  try {
    new URL(body.url);
  } catch {
    return NextResponse.json({ error: "invalid url" }, { status: 400 });
  }
  const transport = body.transport === "sse" ? "sse" : "http";
  const headers =
    body.headers && typeof body.headers === "object"
      ? Object.fromEntries(
          Object.entries(body.headers)
            .filter(([k, v]) => k && typeof v === "string" && v.trim())
            .map(([k, v]) => [String(k), String(v)]),
        )
      : undefined;
  const server = await addMcpServer({
    name: body.name.trim().slice(0, 60),
    url: body.url.trim(),
    transport,
    headers: headers && Object.keys(headers).length ? headers : undefined,
  });
  // Redact on the way back out too.
  const { headers: _h, ...safe } = server;
  return NextResponse.json({ server: { ...safe, hasAuth: !!_h } }, { status: 201 });
}

// PATCH /api/mcp { id, enabled } — enable/disable a server without removing it.
export async function PATCH(req: Request): Promise<NextResponse> {
  const body = (await req.json().catch(() => ({}))) as { id?: string; enabled?: boolean };
  if (!body.id || typeof body.enabled !== "boolean") {
    return NextResponse.json({ error: "id and enabled required" }, { status: 400 });
  }
  await setMcpServerEnabled(body.id, body.enabled);
  return NextResponse.json({ ok: true });
}

// DELETE /api/mcp?id=<name> — remove a server.
export async function DELETE(req: Request): Promise<NextResponse> {
  const id = new URL(req.url).searchParams.get("id");
  if (!id) return NextResponse.json({ error: "id required" }, { status: 400 });
  await removeMcpServer(id);
  return NextResponse.json({ ok: true });
}
