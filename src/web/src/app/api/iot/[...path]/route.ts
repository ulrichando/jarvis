/**
 * /api/iot/* — the web front door for the IoT discovery sidecar.
 *
 * Auth: inherited, no token wiring — same pattern as /api/computer-use.
 * The Devices page is login-gated (proxy.ts page gate); same-origin fetch
 * from it passes the /api/* gate via the `Sec-Fetch-Site: same-origin`
 * carve-out in proxy.ts. Non-browser callers still need the bearer.
 * Deliberately NOT on any proxy allowlist — the default gate is the point.
 *
 * Forwards GET/POST verbatim to the sidecar (bin/jarvis-iot, :8779):
 *   GET  /api/iot/devices                → device inventory
 *   POST /api/iot/scan                   → rescan the LAN
 *   POST /api/iot/devices/{key}/command  → 501 in Phase 1 (discovery-only)
 */

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

const IOT_BASE = process.env.JARVIS_IOT_URL ?? 'http://127.0.0.1:8779'

type Ctx = { params: Promise<{ path: string[] }> }

async function forward(req: Request, ctx: Ctx): Promise<Response> {
  const { path } = await ctx.params
  const search = new URL(req.url).search
  // Encode each segment: device keys are MACs (colons) today, but this keeps
  // any future key shape from smuggling path syntax into the sidecar URL.
  const target = `${IOT_BASE}/${path.map(encodeURIComponent).join('/')}${search}`

  let upstream: Response
  try {
    upstream = await fetch(target, {
      method: req.method,
      headers: { 'content-type': req.headers.get('content-type') ?? 'application/json' },
      body: req.method === 'POST' ? await req.text() : undefined,
      // Scans fan out over the LAN and take several seconds; give headroom
      // beyond the sidecar's own scan timeout, but never hang the page.
      signal: AbortSignal.timeout(30_000),
    })
  } catch {
    return Response.json(
      {
        error:
          'IoT discovery service is not running on :8779 — run `bin/jarvis-iot` or start jarvis-iot.service.',
      },
      { status: 502 },
    )
  }

  // Pass the sidecar's JSON + status straight through (incl. the Phase 1
  // 501 on /command — the UI shows that truthfully).
  const body = await upstream.text()
  return new Response(body, {
    status: upstream.status,
    headers: {
      'content-type': upstream.headers.get('content-type') ?? 'application/json',
    },
  })
}

export async function GET(req: Request, ctx: Ctx): Promise<Response> {
  return forward(req, ctx)
}

export async function POST(req: Request, ctx: Ctx): Promise<Response> {
  return forward(req, ctx)
}
