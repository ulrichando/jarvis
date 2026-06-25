import { NextResponse } from "next/server";
import { getUserId } from "@/lib/auth-helpers";
import { extractBearer } from "@/lib/bridge/auth";

// MCP mutation gate, shared by /api/mcp and /api/mcp/oauth/start. MCP servers
// live in a GLOBAL store (~/.jarvis/mcp.json) — no per-user ownership — but
// POST/PATCH/DELETE are credential-bearing mutations (a server URL + auth
// headers; a hostile entry can exfil headers or SSRF). proxy.ts's /api/* bearer
// gate is the intended network protection but it's opt-in, so the mutations are
// gated app-side too: allow a trusted bearer (CLI) or a real signed-in session;
// reject an unauthenticated caller (no session) when the login gate is active.
// JARVIS_AUTH_DISABLED=1 (proxy.ts's own dev escape) opens it for single-user/dev.
export async function requireMcpAuth(req: Request): Promise<NextResponse | null> {
  if (process.env.JARVIS_AUTH_DISABLED === "1") return null;
  if (extractBearer(req.headers.get("authorization"))) return null;
  const userId = await getUserId(req.headers);
  if (!userId) {
    return NextResponse.json({ error: "authentication required" }, { status: 401 });
  }
  return null;
}
