import { NextResponse } from "next/server";
import { randomUUID } from "node:crypto";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { SSEClientTransport } from "@modelcontextprotocol/sdk/client/sse.js";
import { FileOAuthProvider } from "@/lib/mcp/oauth-provider";
import { savePending, type Transport } from "@/lib/mcp/oauth-store";
import { getUserId } from "@/lib/auth-helpers";
import { LOCAL_USER_ID } from "@/lib/chat/persist";
import { extractBearer } from "@/lib/bridge/auth";

export const runtime = "nodejs";

// Same mutation gate as /api/mcp: a trusted bearer (CLI) or a real signed-in
// session — never the LOCAL_USER_ID fallback when the login gate is active.
async function requireAuth(req: Request): Promise<NextResponse | null> {
  if (process.env.JARVIS_AUTH_DISABLED === "1") return null;
  if (extractBearer(req.headers.get("authorization"))) return null;
  const userId = await getUserId(req.headers);
  if (userId === LOCAL_USER_ID) {
    return NextResponse.json({ error: "authentication required" }, { status: 401 });
  }
  return null;
}

// POST { name, url, transport? } → { authUrl } | { authUrl: null }
// Begins MCP OAuth: discovery + dynamic client registration + PKCE, all driven
// by the SDK. We don't connect for real — we let the provider capture the
// authorization URL and hand it back so the browser can navigate to it.
export async function POST(req: Request): Promise<NextResponse> {
  const denied = await requireAuth(req);
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
