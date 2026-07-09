import { NextResponse } from "next/server";
import { getStore } from "@/lib/bridge/db";
import {
  listPinnedMessageUuids,
  setMessagePin,
} from "@/lib/bridge/store";
import { authorizeBridgeRequest } from "@/lib/bridge/authz";
import { bridgeError } from "@/lib/bridge/errors";

// Authorize access to a session's pins: the CLI worker presents a bearer; the
// browser presents a session cookie, checked against the session's owning
// environment. Unified "session-owner" scope (finding #10) — was an inline
// copy of the ladder. The /api/* network bearer gate is a single shared token,
// so it can't establish per-session ownership; this is the IDOR check it can't
// do. Returns an error response, or null.
async function authorizeSession(
  req: Request,
  sessionId: string,
): Promise<NextResponse | null> {
  return authorizeBridgeRequest(req, { scope: "session-owner", sessionId });
}

// Per-message pins for a /code session, server-synced (survive across
// devices/browsers). GET → { uuids: string[] }; POST { uuid, pinned } toggles.
export async function GET(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params;
  try {
    const denied = await authorizeSession(req, sessionId);
    if (denied) return denied;
    return NextResponse.json({ uuids: listPinnedMessageUuids(getStore(), sessionId) });
  } catch (err) {
    return bridgeError(500, "internal_error", `pins: ${String(err)}`);
  }
}

export async function POST(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params;
  const body = (await req.json().catch(() => null)) as {
    uuid?: string;
    pinned?: boolean;
  } | null;
  if (!body?.uuid || typeof body.pinned !== "boolean") {
    return bridgeError(400, "invalid_request", "uuid + pinned required");
  }
  try {
    const denied = await authorizeSession(req, sessionId);
    if (denied) return denied;
    setMessagePin(getStore(), sessionId, body.uuid, body.pinned);
    return NextResponse.json({ ok: true });
  } catch (err) {
    return bridgeError(500, "internal_error", `pins: ${String(err)}`);
  }
}
