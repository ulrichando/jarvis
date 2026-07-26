import { NextResponse } from 'next/server'
import { randomUUID } from 'node:crypto'
import { getStore } from '@/lib/bridge/db'
import {
  appendInbound,
  appendSessionEvent,
  findSession,
  hasInboundUuid,
  mergeWorkerState,
} from '@/lib/bridge/store'
import { clearLiveText, emitInbound, setLiveText } from '@/lib/bridge/events'
import { authorizeSessionToken } from '@/lib/bridge/authz'
import { firePushForResult } from '@/lib/push/fire'
import { bridgeError } from '@/lib/bridge/errors'
import {
  ASK_QUESTION_PENDING_ONLY_CLEAR,
  buildAskQuestionPauseState,
  isAskUserQuestionTool,
} from '@/lib/bridge/askQuestionPause'

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
  const denied = authorizeSessionToken(req, sessionId)
  if (denied) return denied
  const body = (await req.json().catch(() => null)) as {
    worker_epoch?: number
    events?: Array<{ payload?: Record<string, unknown>; ephemeral?: boolean }>
  } | null
  if (!body || !Array.isArray(body.events)) {
    return bridgeError(400, 'invalid_request', 'events array required')
  }
  try {
    const store = getStore()
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
            | {
                subtype?: string
                tool_name?: string
                tool_use_id?: string
                input?: Record<string, unknown>
              }
            | undefined
          const requestId =
            typeof payload.request_id === 'string' ? payload.request_id : ''
          if (r?.subtype === 'can_use_tool' && requestId) {
            // ExitPlanMode is the /ultraplan review gate: the plan must PAUSE
            // for a human decision (approve → execute on web / local →
            // teleport back to the terminal / reject → revise) rather than
            // auto-run. Record the pending permission (request_id ↔
            // tool_use_id) so the /plan route can resolve it with the user's
            // decision, and do NOT auto-approve it here. Every other tool
            // still auto-approves — the container runs bypassPermissions with
            // no human in the loop, so without it the worker blocks forever on
            // the first Write/Bash.
            if (/^exit_?plan_?mode/i.test(r.tool_name ?? '')) {
              // Waiting on a human decision → Dispatch "needs a go-ahead" push.
              firePushForResult(store, sessionId, 'needs_input')
              // Drop any stale AskUserQuestion pause: the worker reports
              // requires_action for this plan gate with no details, so a
              // leftover pending_action (question abandoned without a Stop)
              // would otherwise surface as a phantom question card here.
              mergeWorkerState(store, sessionId, ASK_QUESTION_PENDING_ONLY_CLEAR)
              appendSessionEvent(store, sessionId, {
                type: 'ultraplan_permission',
                payload: {
                  request_id: requestId,
                  tool_use_id:
                    typeof r.tool_use_id === 'string' ? r.tool_use_id : '',
                  // Original input — the approve path replays it verbatim so
                  // the tool reads the plan from disk exactly as the old
                  // auto-approve did (input.plan is absent; plan lives on disk).
                  input: r.input && typeof r.input === 'object' ? r.input : {},
                },
              })
            } else if (isAskUserQuestionTool(r.tool_name)) {
              // AskUserQuestion needs a human to pick options. Auto-approving
              // it (the else branch) replays the input with NO `answers` field,
              // so the tool tells the model "User has answered your questions: ."
              // and the agent stalls with nothing to go on. Instead PAUSE like
              // ExitPlanMode: surface it as a requires_action pending_action so
              // the browser's QuestionsCard renders (code-session.tsx), and
              // do NOT auto-approve — the /messages permission answer delivers
              // {...input, answers} back and unblocks the worker.
              firePushForResult(store, sessionId, 'needs_input')
              mergeWorkerState(
                store,
                sessionId,
                buildAskQuestionPauseState(requestId, r.input),
              )
            } else {
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
      // A result event = the turn finished → Dispatch "task finished" push.
      if (payload.type === 'result') {
        firePushForResult(store, sessionId, 'done')
      }
      appendSessionEvent(store, sessionId, { type: payload.type, payload })
    }
    return NextResponse.json({})
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
