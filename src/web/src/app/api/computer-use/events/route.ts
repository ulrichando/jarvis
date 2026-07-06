/**
 * /api/computer-use/events — reattach to a session's event stream (SSE proxy).
 * Replays buffered events with seq > `after` (default -1 = everything), then
 * follows live while a run is in progress. Same-origin auth via proxy.ts.
 */
export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

const SIDECAR = process.env.JARVIS_COMPUTER_USE_WEB_URL ?? 'http://127.0.0.1:8771'

const SSE_HEADERS = {
  'Content-Type': 'text/event-stream',
  'Cache-Control': 'no-cache, no-transform',
  Connection: 'keep-alive',
  'X-Accel-Buffering': 'no',
} as const

export async function GET(req: Request): Promise<Response> {
  const url = new URL(req.url)
  const sessionId = url.searchParams.get('session_id') ?? ''
  const after = url.searchParams.get('after') ?? '-1'
  if (!sessionId) return Response.json({ error: 'session_id required' }, { status: 400 })

  let upstream: Response
  try {
    const qs = `session_id=${encodeURIComponent(sessionId)}&after=${encodeURIComponent(after)}`
    // req.signal: the browser leaving only drops this subscription — the
    // detached run keeps going sidecar-side.
    upstream = await fetch(`${SIDECAR}/events?${qs}`, { signal: req.signal })
  } catch {
    return Response.json({ error: 'sidecar unreachable' }, { status: 502 })
  }
  if (!upstream.ok || !upstream.body) {
    return new Response(await upstream.text(), { status: upstream.status })
  }
  return new Response(upstream.body, { headers: SSE_HEADERS })
}
