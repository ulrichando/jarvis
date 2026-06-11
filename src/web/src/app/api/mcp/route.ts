import { NextResponse } from "next/server";
import { getUserId } from "@/lib/auth-helpers";
import { listMcpServers, addMcpServer, removeMcpServer } from "@/lib/mcp/store";

// GET /api/mcp — the logged-in user's MCP servers.
export async function GET(req: Request): Promise<NextResponse> {
  const userId = await getUserId(req.headers);
  return NextResponse.json({ servers: await listMcpServers(userId) });
}

// POST /api/mcp { name, url, transport? } — add a server.
export async function POST(req: Request): Promise<NextResponse> {
  const userId = await getUserId(req.headers);
  const body = (await req.json().catch(() => ({}))) as {
    name?: string;
    url?: string;
    transport?: string;
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
  const server = await addMcpServer(userId, {
    name: body.name.trim().slice(0, 60),
    url: body.url.trim(),
    transport,
  });
  return NextResponse.json({ server }, { status: 201 });
}

// DELETE /api/mcp?id=… — remove a server.
export async function DELETE(req: Request): Promise<NextResponse> {
  const userId = await getUserId(req.headers);
  const id = new URL(req.url).searchParams.get("id");
  if (!id) return NextResponse.json({ error: "id required" }, { status: 400 });
  await removeMcpServer(userId, id);
  return NextResponse.json({ ok: true });
}
