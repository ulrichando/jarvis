import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import { appendInbound, listSessionEvents } from '@/lib/bridge/store'
import { extractBearer } from '@/lib/bridge/auth'
import { bridgeError } from '@/lib/bridge/errors'

// CCR-compat event stream — the teleport/ultraplan poller
// (pollRemoteSessionEvents). See ../../environment_providers/route.ts header.

// GET /api/v1/sessions/{id}/events?after_id=<rowid> — return new SDKMessages.
// `after_id` is an OPAQUE cursor to the client (it just echoes it back), so we
// use the monotonic rowid stringified — no UUID layer needed. The worker writes
// each SDKMessage via the /worker/events path with payload === the full message,
// so we return the raw payloads as `data` (the client requires top-level `type`
// + `session_id` on each event; the bridge {cursor,type,payload} wrapper would
// NOT satisfy that — hence this dedicated shape).
export async function GET(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params
  const afterId = new URL(req.url).searchParams.get('after_id')
  const sinceRaw = Number(afterId ?? '0')
  const since = Number.isFinite(sinceRaw) && sinceRaw >= 0 ? sinceRaw : 0
  try {
    const store = getStore()
    const rows = listSessionEvents(store, sessionId, since)
    const data = rows.map((r) => JSON.parse(r.payload_json) as unknown)
    const lastRowid = rows.length ? rows[rows.length - 1].rowid : since
    return NextResponse.json({
      data,
      has_more: false,
      first_id: rows.length ? String(rows[0].rowid) : null,
      // Echo the cursor when there were no new rows so the client's stored
      // cursor persists (an empty page must not reset polling to the start).
      last_id: String(lastRowid),
    })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}

// POST /api/v1/sessions/{id}/events — a web→worker message (e.g. a follow-up
// user turn). Routed to session_inbound (appendInbound) so the polling worker
// delivers it to the agent — NOT appendSessionEvent (transcript only).
export async function POST(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params
  const token = extractBearer(req.headers.get('authorization'))
  if (!token) return bridgeError(401, 'unauthorized', 'Missing bearer')
  const body = (await req.json().catch(() => null)) as {
    events?: Array<{ type?: string; [k: string]: unknown }>
  } | null
  if (!body || !Array.isArray(body.events)) {
    return bridgeError(400, 'invalid_request', 'events array required')
  }
  try {
    const store = getStore()
    for (const event of body.events) {
      if (!event || typeof event !== 'object') continue
      appendInbound(store, sessionId, event)
    }
    return new NextResponse(null, { status: 204 })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
