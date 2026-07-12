import { takePendingVoiceReminders } from "@/lib/scheduled/store";

export const runtime = "nodejs";

// GET /api/scheduled/voice-pending — the LOCAL voice poller pulls fired-but-
// unvoiced scheduled reminders from a remote (VPS) instance and speaks them
// on the box where the voice agent runs. Returns the pending reminders and
// marks them delivered in the same read (no double-delivery across polls).
//
// Auth: a dedicated shared bearer token (JARVIS_SCHEDULED_VOICE_TOKEN), NOT a
// user session — the poller is a headless service. Fail-closed: if the token
// isn't configured, the endpoint refuses everything (never open by default).
// Allowlisted in proxy.ts (SELF_AUTH_GET) so the network gate defers to this
// in-handler check; deployed instances must also exclude it from Cloudflare
// Access, like /api/bridge/*.
export async function GET(req: Request) {
  const expected = process.env.JARVIS_SCHEDULED_VOICE_TOKEN;
  if (!expected) {
    return Response.json(
      { error: "voice-pending disabled (no JARVIS_SCHEDULED_VOICE_TOKEN)" },
      { status: 503 },
    );
  }
  const auth = req.headers.get("authorization") ?? "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7).trim() : "";
  // Constant-time-ish compare: length check + char accumulation. Node's
  // timingSafeEqual needs equal-length buffers, so guard length first.
  if (token.length !== expected.length || token !== expected) {
    return new Response("Unauthorized", { status: 401 });
  }
  return Response.json({ reminders: takePendingVoiceReminders() });
}
