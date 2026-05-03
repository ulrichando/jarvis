// GET /api/events/stream/[sessionId]
//
// Server-Sent Events endpoint. Subscribes to broadcasts:conversation
// in Redis, filters by session_id, pushes one `data: <json>\n\n`
// per matching event. Replaces the live half of
// useQuery(api.turns.bySession, ...).
//
// Reconnect: browsers automatically include Last-Event-ID; we resume
// XREAD from that id so no event is missed across a flaky connection.

import Redis from 'ioredis'

const BROADCASTS_STREAM = 'broadcasts:conversation'

export async function GET(
  req: Request,
  { params }: { params: Promise<{ sessionId: string }> },
) {
  const { sessionId } = await params
  // '$' means "only events arriving from this point on". Browser
  // reconnect supplies Last-Event-ID via header → resume from id.
  const lastId = req.headers.get('last-event-id') ?? '$'
  const redis = new Redis(process.env.JARVIS_HUB_URL ?? 'redis://127.0.0.1:6379')

  let cancelled = false
  const onAbort = () => {
    cancelled = true
    redis.quit().catch(() => {})
  }
  req.signal.addEventListener('abort', onAbort)

  const stream = new ReadableStream({
    async start(controller) {
      const enc = new TextEncoder()
      const send = (chunk: string) => {
        if (!cancelled) {
          try {
            controller.enqueue(enc.encode(chunk))
          } catch {
            // Channel already closed by abort; swallow.
          }
        }
      }

      // Heartbeat every 15s so intermediaries don't time out the connection.
      const heartbeat = setInterval(() => send(': heartbeat\n\n'), 15_000)

      let cursor = lastId
      try {
        while (!cancelled) {
          const resp = await redis.xread(
            'BLOCK', 5000,
            'STREAMS', BROADCASTS_STREAM, cursor,
          ) as Array<[string, Array<[string, string[]]>]> | null

          if (!resp) continue
          for (const [, entries] of resp) {
            for (const [id, fields] of entries) {
              cursor = id
              // fields is ['data', '<json>'] — find the value paired with 'data'
              const dataIdx = fields.indexOf('data')
              if (dataIdx < 0 || dataIdx + 1 >= fields.length) continue
              let evt: { session_id?: string }
              try {
                evt = JSON.parse(fields[dataIdx + 1])
              } catch {
                continue
              }
              if (evt.session_id !== sessionId) continue
              send(`id: ${id}\ndata: ${JSON.stringify(evt)}\n\n`)
            }
          }
        }
      } catch (err) {
        send(
          `event: error\ndata: ${JSON.stringify({ message: String(err) })}\n\n`,
        )
      } finally {
        clearInterval(heartbeat)
        try {
          controller.close()
        } catch {
          // Already closed.
        }
        redis.quit().catch(() => {})
      }
    },
  })

  return new Response(stream, {
    headers: {
      'content-type': 'text/event-stream',
      'cache-control': 'no-store, no-transform',
      // Disable nginx buffering if proxied.
      'x-accel-buffering': 'no',
    },
  })
}
