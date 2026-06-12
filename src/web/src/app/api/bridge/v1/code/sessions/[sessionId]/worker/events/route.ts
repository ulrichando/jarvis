import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import {
  appendSessionEvent,
  findSession,
  hasInboundUuid,
  validateSessionToken,
} from '@/lib/bridge/store'
import { extractBearer } from '@/lib/bridge/auth'
import { bridgeError } from '@/lib/bridge/errors'

// POST /api/bridge/v1/code/sessions/{id}/worker/events — the CLI's outbound
// transcript (CCRClient writeEvent → SerialBatchEventUploader). Body:
// { worker_epoch, events: [{ payload: <SDK message>, ephemeral? }] }.
// Payloads land in session_events so the /code session view can tail them.
// stream_event snapshots (live-typing deltas) are ephemeral by design — the
// final assistant message follows — so they are not persisted.
export async function POST(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params
  const token = extractBearer(req.headers.get('authorization'))
  if (!token) return bridgeError(401, 'unauthorized', 'Missing bearer')
  const body = (await req.json().catch(() => null)) as {
    worker_epoch?: number
    events?: Array<{ payload?: Record<string, unknown>; ephemeral?: boolean }>
  } | null
  if (!body || !Array.isArray(body.events)) {
    return bridgeError(400, 'invalid_request', 'events array required')
  }
  try {
    const store = getStore()
    if (!validateSessionToken(store, sessionId, token)) {
      return bridgeError(401, 'unauthorized', 'Invalid session token')
    }
    const session = findSession(store, sessionId)
    if (
      typeof body.worker_epoch === 'number' &&
      session &&
      body.worker_epoch !== session.worker_epoch
    ) {
      return bridgeError(409, 'epoch_mismatch', 'Worker epoch is stale')
    }
    for (const event of body.events) {
      const payload = event?.payload
      if (!payload || typeof payload.type !== 'string') continue
      // stream_event = live-typing snapshots (the final message follows);
      // keep_alive = container-lease liveness pings; control_request/
      // control_response = protocol traffic (mode switches, permission
      // verdicts). None of these are transcript.
      if (
        event.ephemeral ||
        payload.type === 'stream_event' ||
        payload.type === 'keep_alive' ||
        payload.type === 'control_request' ||
        payload.type === 'control_response'
      ) {
        continue
      }
      // --replay-user-messages makes the worker echo back user messages it
      // received over the stream. Messages the web client itself sent are
      // already shown as user_prompt rows — drop the echo by uuid so the
      // transcript doesn't show them twice. (REPL history flushes have
      // locally-generated uuids and pass through, which is what populates
      // the transcript on attach.)
      if (
        payload.type === 'user' &&
        typeof payload.uuid === 'string' &&
        hasInboundUuid(store, sessionId, payload.uuid)
      ) {
        continue
      }
      appendSessionEvent(store, sessionId, { type: payload.type, payload })
    }
    return NextResponse.json({})
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
