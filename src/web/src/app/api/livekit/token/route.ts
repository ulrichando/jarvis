import { randomUUID } from "node:crypto";
import { NextResponse } from "next/server";
import { AccessToken } from "livekit-server-sdk";
import { getUserId } from "@/lib/auth-helpers";

// Long enough for an extended voice conversation; the phone re-POSTs here for
// a fresh token/room on every new voice session, so no refresh path is needed.
const TOKEN_TTL = "6h";

/**
 * POST /api/livekit/token — mint a LiveKit room-join token for the logged-in
 * user's realtime voice session (P3 of the LiveKit voice plan; see
 * docs/superpowers/specs/2026-07-12-livekit-realtime-voice-design.md in the
 * jarvis-android repo).
 *
 * Auth: better-auth session cookie, exactly like the sibling
 * /api/bridge/proxy-token route. The Android app sends the cookie plus the
 * `sec-fetch-site: same-origin` marker, so this passes proxy.ts through the
 * same same-origin+cookie carve-out the bridge routes use — no proxy allowlist
 * entry needed.
 *
 * Room: per-user AND per-request (`voice-<userId>-<uuid8>`). A fresh room per
 * call — rather than a deterministic per-user name — means a reconnecting
 * phone never lands in a stale room alongside a dead agent session; the
 * voice-agent worker auto-dispatches into any new room, so no explicit
 * dispatch call is needed here.
 *
 * Env (set in .env.production; secret lives in /opt/jarvis/livekit/keys.env):
 *   LIVEKIT_API_KEY / LIVEKIT_API_SECRET — the LiveKit server's key pair.
 *   LIVEKIT_URL — public signaling URL (wss://livekit.0wlan.com).
 */
export async function POST(req: Request): Promise<NextResponse> {
  try {
    const userId = await getUserId(req.headers);
    if (!userId) {
      return NextResponse.json(
        { error: "authentication required" },
        { status: 401 },
      );
    }

    const apiKey = process.env.LIVEKIT_API_KEY?.trim();
    const apiSecret = process.env.LIVEKIT_API_SECRET?.trim();
    const url = process.env.LIVEKIT_URL?.trim();
    if (!apiKey || !apiSecret || !url) {
      return NextResponse.json(
        { error: "LiveKit is not configured on this server" },
        { status: 503 },
      );
    }

    // Optional body: { conversationId?, model? }. conversationId = the chat the
    // phone opened voice from, so the agent seeds THAT conversation's history
    // (#15); model = the user's Settings voice-model pick (#19). Both ride the
    // participant metadata, which the agent reads on join. Standalone voice (or
    // an old client) sends neither → empty metadata → default behaviour.
    let conversationId: string | undefined;
    let model: string | undefined;
    try {
      const body = (await req.json()) as { conversationId?: unknown; model?: unknown } | null;
      if (body && typeof body === "object") {
        if (typeof body.conversationId === "string" && body.conversationId.trim()) {
          conversationId = body.conversationId.trim();
        }
        if (typeof body.model === "string" && body.model.trim()) {
          model = body.model.trim();
        }
      }
    } catch {
      // no / invalid JSON body → standalone voice, no metadata
    }
    const metadata =
      conversationId || model
        ? JSON.stringify({
            ...(conversationId ? { conversationId } : {}),
            ...(model ? { model } : {}),
          })
        : undefined;

    const room = `voice-${userId}-${randomUUID().slice(0, 8)}`;
    const at = new AccessToken(apiKey, apiSecret, {
      identity: userId,
      ttl: TOKEN_TTL,
      ...(metadata ? { metadata } : {}),
    });
    at.addGrant({
      roomJoin: true,
      room,
      canPublish: true,
      canSubscribe: true,
      // Lets the phone set its own `voice` participant attribute (the chosen
      // Edge voice) so the realtime voice agent reads it and speaks in that
      // voice (agent._resolve_tts_voice). Was a box-only hand-edit; folded into
      // the repo here so the deploy doesn't regress phone voice selection.
      canUpdateOwnMetadata: true,
    });
    const token = await at.toJwt();

    return NextResponse.json({ token, url, room });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}
