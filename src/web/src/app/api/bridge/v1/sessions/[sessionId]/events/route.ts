import { NextResponse } from 'next/server'
import { extractBearer } from '@/lib/bridge/auth'
import { getLiveText } from '@/lib/bridge/events'
import { getStore } from '@/lib/bridge/db'
import {
  appendSessionEvent,
  findSession,
  listSessionEvents,
} from '@/lib/bridge/store'
import { maybeResumeOnAttach } from '@/lib/bridge/resume'
import { bridgeError } from '@/lib/bridge/errors'

export async function POST(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params
  const token = extractBearer(req.headers.get('authorization'))
  // v1: accept any non-empty bearer for session events. Sub-project 3 will
  // tighten this to validate against the work row's secret_b64url
  // (session_ingress_token).
  if (!token) return bridgeError(401, 'unauthorized', 'Missing bearer')
  const body = (await req.json().catch(() => null)) as {
    events?: Array<{ type: string; [k: string]: unknown }>
  } | null
  if (!body || !Array.isArray(body.events)) {
    return bridgeError(400, 'invalid_request', 'events array required')
  }
  try {
    const store = getStore()
    for (const event of body.events) {
      // v1: silently skip events without a string `type` rather than
      // returning 400 for the whole batch. Sub-project 3 will switch to
      // strict validation once the canonical event-type set is locked
      // down, so a malformed event rejects with 400 + index in the body.
      if (typeof event.type !== 'string') continue
      appendSessionEvent(store, sessionId, {
        type: event.type,
        payload: event,
      })
    }
    return new NextResponse(null, { status: 204 })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}

// GET /api/bridge/v1/sessions/{id}/events?since=<rowid> — tail a session's
// event stream for the /code session view. `since` is the rowid cursor from the
// previous poll (0 = from the start). Returns events + the next cursor.
export async function GET(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params
  const sinceRaw = Number(new URL(req.url).searchParams.get('since') ?? '0')
  const since = Number.isFinite(sinceRaw) && sinceRaw >= 0 ? sinceRaw : 0
  // Reopen = a chance to reconnect a worker that died while away (e.g. a
  // web-server restart). Fire-and-forget + internally gated, so the hot poll
  // path stays fast and a live worker is never disturbed.
  maybeResumeOnAttach(sessionId)
  try {
    const store = getStore()
    const rows = listSessionEvents(store, sessionId, since)
    const events = rows.map((r) => ({
      cursor: r.rowid,
      type: r.type,
      payload: JSON.parse(r.payload_json) as unknown,
      created_at: r.created_at,
    }))
    const cursor = rows.length ? rows[rows.length - 1].rowid : since
    // Worker runtime state (PUT /worker): status drives the UI spinner,
    // requires_action_details drives the permission approve/deny card.
    const session = findSession(store, sessionId)
    let worker: Record<string, unknown> | null = null
    if (session?.worker_state_json) {
      try {
        worker = JSON.parse(session.worker_state_json) as Record<
          string,
          unknown
        >
      } catch {
        worker = null
      }
    }
    // In-flight assistant text (ephemeral stream_event snapshots) — lets the
    // UI show the reply as it streams; cleared server-side when the final
    // assistant message lands in the transcript.
    return NextResponse.json({
      events,
      cursor,
      worker,
      live: getLiveText(sessionId),
    })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
