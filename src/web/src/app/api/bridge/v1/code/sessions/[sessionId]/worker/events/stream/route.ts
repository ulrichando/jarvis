import { getStore } from '@/lib/bridge/db'
import {
  getInboundFloorSeq,
  listInboundSince,
  validateSessionToken,
} from '@/lib/bridge/store'
import { waitForInbound } from '@/lib/bridge/events'
import { extractBearer } from '@/lib/bridge/auth'
import { bridgeError } from '@/lib/bridge/errors'

export const dynamic = 'force-dynamic'

const KEEPALIVE_MS = 15_000 // SSETransport's liveness budget is 45s

// GET /api/bridge/v1/code/sessions/{id}/worker/events/stream — the CCR v2
// SSE read stream (SSETransport). Delivers queued inbound (web → CLI)
// messages as `client_event` frames:
//
//   id: <seq>
//   event: client_event
//   data: {"sequence_num":N,"event_id":"N","event_type":"client_event","payload":{...}}
//
// Resumption: ?from_sequence_num= (also Last-Event-ID header). Keepalive
// comments every 15s reset the client's 45s liveness timer.
export async function GET(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<Response> {
  const { sessionId } = await ctx.params
  const token = extractBearer(req.headers.get('authorization'))
  if (!token) return bridgeError(401, 'unauthorized', 'Missing bearer')
  const store = getStore()
  if (!validateSessionToken(store, sessionId, token)) {
    return bridgeError(401, 'unauthorized', 'Invalid session token')
  }

  const url = new URL(req.url)
  const fromParam =
    url.searchParams.get('from_sequence_num') ??
    req.headers.get('last-event-id') ??
    '0'
  let cursor = Number.parseInt(fromParam, 10)
  if (!Number.isFinite(cursor) || cursor < 0) cursor = 0
  // Resume clamp: a relaunched worker reconnects from seq 0 (fresh CLI session)
  // and would replay already-processed inbound — re-running the original task.
  // resumeContainerWorker raises this floor to the inbound tip so a resumed
  // worker starts idle. First launch: floor 0 → the seeded prompt still streams.
  const floor = getInboundFloorSeq(store, sessionId)
  if (cursor < floor) cursor = floor

  const encoder = new TextEncoder()
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const write = (text: string): void =>
        controller.enqueue(encoder.encode(text))
      write(': connected\n\n')
      try {
        while (!req.signal.aborted) {
          const rows = listInboundSince(store, sessionId, cursor)
          for (const row of rows) {
            cursor = row.seq
            const frame = {
              sequence_num: row.seq,
              event_id: String(row.seq),
              event_type: 'client_event',
              payload: JSON.parse(row.payload_json) as unknown,
            }
            write(
              `id: ${row.seq}\nevent: client_event\ndata: ${JSON.stringify(frame)}\n\n`,
            )
          }
          const woke = await waitForInbound(sessionId, KEEPALIVE_MS)
          if (!woke) write(': keepalive\n\n')
        }
      } catch {
        /* client disconnected mid-write */
      } finally {
        try {
          controller.close()
        } catch {
          /* already closed */
        }
      }
    },
  })

  return new Response(stream, {
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no',
    },
  })
}
