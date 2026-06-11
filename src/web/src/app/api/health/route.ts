/**
 * GET /api/health — unauthenticated liveness probe.
 *
 * Allowlisted in src/proxy.ts PUBLIC_PATHS (the entry predated this
 * route as "reserved for future"). The desktop tray's probe_jarvis_web()
 * hits this to decide whether a JARVIS web is already running before
 * "Open in Browser" / "View Logs" — it sends no bearer token, so the
 * target must work with JARVIS_REQUIRE_LOCAL_AUTH=1. Deliberately
 * returns no state beyond identity: nothing here is worth gating.
 */
export async function GET() {
  return Response.json({ ok: true, service: "jarvis-web" });
}
