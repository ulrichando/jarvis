import { NextResponse } from "next/server";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { SSEClientTransport } from "@modelcontextprotocol/sdk/client/sse.js";
import { FileOAuthProvider } from "@/lib/mcp/oauth-provider";
import { getPending, delPending, getServerAuth } from "@/lib/mcp/oauth-store";

export const runtime = "nodejs";

// GET ?code&state — the provider redirects the browser here after sign-in. We
// exchange the code for tokens (the SDK's finishAuth → provider.saveTokens,
// which persists to oauth-store AND mirrors the access token into mcp.json),
// then bounce back to /settings. CSRF-protected by the unguessable `state` we
// stored at start; no token is in the URL beyond the one-time auth `code`.
export async function GET(req: Request): Promise<Response> {
  const u = new URL(req.url);
  const code = u.searchParams.get("code");
  const state = u.searchParams.get("state");
  // Build the return URL from the real Host header, NOT u.origin: Next can
  // normalize req.url's host to "localhost" even when reached via 127.0.0.1,
  // and the better-auth session cookie is bound to 127.0.0.1 — landing on
  // localhost would bounce through /login. Stay on whatever host got us here
  // (which equals the registered redirect_uri's host).
  const host = req.headers.get("host");
  const base = host ? `http://${host}` : u.origin;
  const settings = new URL("/settings", base);

  const fail = (msg: string) => {
    settings.searchParams.set("mcp", "error");
    settings.searchParams.set("mcp_msg", msg.slice(0, 140));
    return NextResponse.redirect(settings);
  };

  const provErr = u.searchParams.get("error");
  if (provErr) return fail(`provider error: ${provErr}`);
  if (!code || !state) return fail("missing code or state");

  const pending = await getPending(state);
  if (!pending) return fail("authorization expired or unknown — try again");

  const provider = new FileOAuthProvider({
    name: pending.name,
    state,
    url: pending.url,
    transport: pending.transport,
    redirectUri: pending.redirectUri,
    seed: { clientInfo: pending.clientInfo, codeVerifier: pending.codeVerifier },
  });
  const tUrl = new URL(pending.url);
  const transport =
    pending.transport === "sse"
      ? new SSEClientTransport(tUrl, { authProvider: provider })
      : new StreamableHTTPClientTransport(tUrl, { authProvider: provider });

  try {
    await transport.finishAuth(code);
  } catch (e) {
    return fail(`token exchange failed: ${e instanceof Error ? e.message : String(e)}`);
  } finally {
    await transport.close().catch(() => {});
  }

  const auth = await getServerAuth(pending.name);
  if (!auth?.tokens.access_token) return fail("no access token after exchange");

  // saveTokens (called inside finishAuth) already mirrored the token into
  // mcp.json via upsertOAuthServer; just clean up the pending record.
  await delPending(state);
  settings.searchParams.set("mcp", "connected");
  settings.searchParams.set("mcp_name", pending.name);
  return NextResponse.redirect(settings);
}
