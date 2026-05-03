// GET /api/events/stream/settings
//
// Server-Sent Events endpoint. Subscribes to broadcasts:settings
// in Redis and pushes one `data: <json>\n\n` per event. Unlike the
// per-session conversation SSE, this stream is GLOBAL — no filter.
//
// Reconnect: browsers automatically include Last-Event-ID; we
// resume XREAD from that id so no event is missed.

import Redis from 'ioredis'

const BROADCASTS_STREAM = 'broadcasts:settings'

export async function GET(req: Request) {
  const lastId = req.headers.get('last-event-id') ?? '$'
  const redis = new Redis(process.env.JARVIS_HUB_URL ?? 'redis://127.0.0.1:6379')

  let cancelled = false
  req.signal.addEventListener('abort', () => {
    cancelled = true
    redis.quit().catch(() => {})
  })

  const stream = new ReadableStream({
    async start(controller) {
      const enc = new TextEncoder()
      const send = (chunk: string) => {
        if (!cancelled) {
          try { controller.enqueue(enc.encode(chunk)) } catch { /* closed */ }
        }
      }

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
              const dataIdx = fields.indexOf('data')
              if (dataIdx < 0 || dataIdx + 1 >= fields.length) continue
              send(`id: ${id}\ndata: ${fields[dataIdx + 1]}\n\n`)
            }
          }
        }
      } catch (err) {
        send(
          `event: error\ndata: ${JSON.stringify({ message: String(err) })}\n\n`,
        )
      } finally {
        clearInterval(heartbeat)
        try { controller.close() } catch { /* closed */ }
        redis.quit().catch(() => {})
      }
    },
  })

  return new Response(stream, {
    headers: {
      'content-type': 'text/event-stream',
      'cache-control': 'no-store, no-transform',
      'x-accel-buffering': 'no',
    },
  })
}
