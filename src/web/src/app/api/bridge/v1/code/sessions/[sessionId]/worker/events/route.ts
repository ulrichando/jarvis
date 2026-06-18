import { NextResponse } from 'next/server'
import { randomUUID } from 'node:crypto'
import { getStore } from '@/lib/bridge/db'
import {
  appendInbound,
  appendSessionEvent,
  findSession,
  hasInboundUuid,
  validateSessionToken,
} from '@/lib/bridge/store'
import { clearLiveText, emitInbound, setLiveText } from '@/lib/bridge/events'
import { extractBearer } from '@/lib/bridge/auth'
import { bridgeError } from '@/lib/bridge/errors'

// Structural slice of an ephemeral stream_event carrying a full-so-far
// text snapshot (the CLI coalesces deltas per block — ccrClient.ts).
type StreamEventPayload = {
  parent_tool_use_id?: string | null
  event?: {
    type?: string
    index?: number
    delta?: { type?: string; text?: string }
  }
}

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
      // verdicts). None of these are transcript — but foreground text
      // snapshots feed the in-memory live-typing buffer the events poll
      // serves while the turn streams.
      if (
        event.ephemeral ||
        payload.type === 'stream_event' ||
        payload.type === 'keep_alive' ||
        payload.type === 'control_request' ||
        payload.type === 'control_response'
      ) {
        if (payload.type === 'stream_event') {
          const se = payload as StreamEventPayload
          if (
            (se.parent_tool_use_id ?? null) === null &&
            se.event?.type === 'content_block_delta' &&
            se.event.delta?.type === 'text_delta' &&
            typeof se.event.delta.text === 'string' &&
            typeof se.event.index === 'number'
          ) {
            setLiveText(sessionId, se.event.index, se.event.delta.text)
          }
        }
        // can_use_tool: the CLI is asking the host to approve a tool call.
        // Container sessions are isolated, autonomous sandboxes dispatched with
        // bypassPermissions — there is no human in the loop to click "allow",
        // so the server auto-approves. Without this the worker blocks forever
        // on the first permission-gated tool (Write, Bash…) and the session
        // view spins indefinitely. Browser-driven local sessions answer
        // permissions through the messages route instead, so gate on
        // container_json (only set for launched container sessions).
        if (payload.type === 'control_request' && session?.container_json) {
          const r = payload.request as
            | { subtype?: string; input?: Record<string, unknown> }
            | undefined
          const requestId =
            typeof payload.request_id === 'string' ? payload.request_id : ''
          if (r?.subtype === 'can_use_tool' && requestId) {
            appendInbound(store, sessionId, {
              type: 'control_response',
              uuid: randomUUID(),
              response: {
                subtype: 'success',
                request_id: requestId,
                response: {
                  behavior: 'allow',
                  updatedInput:
                    r.input && typeof r.input === 'object' ? r.input : {},
                },
              },
            })
            emitInbound(sessionId)
          }
        }
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
      // The complete message supersedes any in-flight snapshot.
      if (payload.type === 'assistant' || payload.type === 'result') {
        clearLiveText(sessionId)
      }
      appendSessionEvent(store, sessionId, { type: payload.type, payload })
    }
    return NextResponse.json({})
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
