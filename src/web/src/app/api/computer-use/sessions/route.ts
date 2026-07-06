/**
 * /api/computer-use/sessions — list the sidecar's run sessions (GET proxy).
 * Same-origin from the logged-in page (inherits proxy.ts auth). The page uses
 * this to reattach to a live run and to show what's running/idle.
 */
export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

const SIDECAR = process.env.JARVIS_COMPUTER_USE_WEB_URL ?? 'http://127.0.0.1:8771'

export async function GET(): Promise<Response> {
  try {
    const r = await fetch(`${SIDECAR}/sessions`, {
      signal: AbortSignal.timeout(3000),
      cache: 'no-store',
    })
    return new Response(await r.text(), {
      status: r.status,
      headers: { 'content-type': 'application/json' },
    })
  } catch {
    // Sidecar down → an empty list, not an error page — the page already
    // surfaces sidecar-down through the /api/computer-use status probe.
    return Response.json({ sessions: [] })
  }
}
