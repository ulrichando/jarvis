import { NextResponse } from "next/server";
import { testMcpServer } from "@/lib/mcp/client";

// POST /api/mcp/test { url, transport?, name? } — connect + list tools, so the
// user can validate a server before/after adding it.
export async function POST(req: Request): Promise<NextResponse> {
  const body = (await req.json().catch(() => ({}))) as {
    name?: string;
    url?: string;
    transport?: string;
  };
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
    enabled: true,
    createdAt: 0,
  });
  return NextResponse.json(r);
}
