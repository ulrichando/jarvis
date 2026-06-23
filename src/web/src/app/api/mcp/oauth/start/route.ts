import { NextResponse } from "next/server";
import { randomUUID } from "node:crypto";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { SSEClientTransport } from "@modelcontextprotocol/sdk/client/sse.js";
import { FileOAuthProvider } from "@/lib/mcp/oauth-provider";
import { savePending, type Transport } from "@/lib/mcp/oauth-store";
import { requireMcpAuth } from "@/lib/mcp/authz";

export const runtime = "nodejs";

// POST { name, url, transport? } → { authUrl } | { authUrl: null }
// Begins MCP OAuth: discovery + dynamic client registration + PKCE, all driven
// by the SDK. We don't connect for real — we let the provider capture the
// authorization URL and hand it back so the browser can navigate to it.
export async function POST(req: Request): Promise<NextResponse> {
  const denied = await requireMcpAuth(req);
  if (denied) return denied;

  const body = (await req.json().catch(() => ({}))) as {
    name?: string;
    url?: string;
    transport?: string;
  };
  const name = body.name?.trim();
  const url = body.url?.trim();
  if (!name || !url) {
    return NextResponse.json({ error: "name and url required" }, { status: 400 });
  }
  try {
    new URL(url);
  } catch {
    return NextResponse.json({ error: "invalid url" }, { status: 400 });
  }
  const transport: Transport = body.transport === "sse" ? "sse" : "http";

  const origin =
    req.headers.get("origin") ?? `http://${req.headers.get("host") ?? "127.0.0.1:3000"}`;
  const redirectUri = `${origin}/api/mcp/oauth/callback`;
  const state = randomUUID();
  // Persist the pending context FIRST so the provider's saveClientInformation /
  // saveCodeVerifier callbacks (fired during connect) can patch into it.
  await savePending(state, { name, url, transport, redirectUri });

  const provider = new FileOAuthProvider({ name, state, url, transport, redirectUri });
  const tUrl = new URL(url);
  const transportObj =
    transport === "sse"
      ? new SSEClientTransport(tUrl, { authProvider: provider })
      : new StreamableHTTPClientTransport(tUrl, { authProvider: provider });
  const client = new Client({ name: "jarvis-web", version: "1.0.0" });

  try {
    await client.connect(transportObj);
    // Connected with no auth required — nothing to sign into.
    await client.close().catch(() => {});
    return NextResponse.json({ authUrl: null, note: "server did not require sign-in" });
  } catch {
    // Expected path: the SDK threw UnauthorizedError after the provider captured
    // the authorization URL (discovery + DCR + PKCE all happened in here).
    await client.close().catch(() => {});
    if (provider.capturedAuthUrl) {
      return NextResponse.json({ authUrl: provider.capturedAuthUrl.toString() });
    }
    return NextResponse.json(
      { error: "could not start authorization (server may not support OAuth/DCR)" },
      { status: 502 },
    );
  }
}
