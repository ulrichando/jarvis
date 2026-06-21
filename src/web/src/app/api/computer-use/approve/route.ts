/**
 * /api/computer-use/approve — resolve a pending permission prompt from the
 * computer-use loop. Same-origin from the logged-in page (inherits proxy.ts
 * auth). Body: { request_id, decision } where decision ∈ once | session | deny.
 * Proxies to the sidecar's /approve.
 */
export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

const SIDECAR = process.env.JARVIS_COMPUTER_USE_WEB_URL ?? 'http://127.0.0.1:8771'

export async function POST(req: Request): Promise<Response> {
  let body: unknown = {}
  try {
    body = await req.json()
  } catch {
    /* empty body → {} */
  }
  try {
    const r = await fetch(`${SIDECAR}/approve`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body ?? {}),
      signal: AbortSignal.timeout(5000),
    })
    return new Response(await r.text(), {
      status: r.status,
      headers: { 'content-type': 'application/json' },
    })
  } catch {
    return Response.json({ ok: false, error: 'sidecar unreachable' }, { status: 502 })
  }
}
