// Jarvis Desktop Bridge Server
//
// Exposes the WebSocket + REST API that the Tauri desktop app expects,
// and routes conversations through the Jarvis proxy on port 4000.
//
// Endpoints:
//   GET  /health                → { status: 'ok' }
//   GET  /ws?client=desktop     → WebSocket for chat + status events
//   POST /api/mute              → { muted: boolean }
//
// WS message shapes:
//   Client → server: { type: 'query', text: string }
//   Server → client: { type: 'status', status: 'thinking' | 'speaking' | 'idle' }
//                    { type: 'chat_response', text: string }
//                    { type: 'brain_ready' }
//                    { type: 'voice_muted', muted: boolean }

const PORT       = parseInt(process.env.JARVIS_BRIDGE_PORT ?? '8765')
const PROXY_URL  = process.env.JARVIS_PROXY_URL ?? 'http://localhost:4000'
const MODEL      = process.env.JARVIS_BRIDGE_MODEL ?? 'deepseek-chat'

const clients = new Set<WebSocket>()
let muted = false

function broadcast(msg: unknown): void {
  const data = JSON.stringify(msg)
  for (const ws of clients) {
    try { ws.send(data) } catch { /* closed */ }
  }
}

async function handleQuery(ws: WebSocket, text: string): Promise<void> {
  broadcast({ type: 'status', status: 'thinking' })

  const body = {
    model: MODEL,
    max_tokens: 4000,
    stream: false,
    messages: [{ role: 'user', content: text }],
    system: 'You are Jarvis, a helpful AI assistant. Keep responses concise.',
  }

  try {
    const resp = await fetch(`${PROXY_URL}/v1/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const data = await resp.json() as any
    const reply = data?.content?.[0]?.text ?? '(no response)'

    ws.send(JSON.stringify({ type: 'chat_response', text: reply }))
    broadcast({ type: 'status', status: 'speaking' })
    setTimeout(() => broadcast({ type: 'status', status: 'idle' }), 800)
  } catch (e: any) {
    ws.send(JSON.stringify({ type: 'chat_response', text: `Error: ${e.message}` }))
    broadcast({ type: 'status', status: 'idle' })
  }
}

const server = Bun.serve({
  port: PORT,
  fetch(req, server) {
    const url = new URL(req.url)

    // WebSocket upgrade
    if (url.pathname === '/ws') {
      if (server.upgrade(req)) return
      return new Response('WebSocket upgrade failed', { status: 400 })
    }

    if (url.pathname === '/health') {
      return Response.json({ status: 'ok' })
    }

    if (url.pathname === '/api/mute' && req.method === 'POST') {
      muted = !muted
      broadcast({ type: 'voice_muted', muted })
      return Response.json({ muted })
    }

    return new Response('Not found', { status: 404 })
  },
  websocket: {
    open(ws) {
      clients.add(ws as unknown as WebSocket)
      console.log(`[bridge] client connected (${clients.size} total)`)
      ws.send(JSON.stringify({ type: 'brain_ready' }))
      ws.send(JSON.stringify({ type: 'status', status: 'idle' }))
    },
    close(ws) {
      clients.delete(ws as unknown as WebSocket)
      console.log(`[bridge] client disconnected (${clients.size} total)`)
    },
    async message(ws, raw) {
      let msg: any
      try { msg = JSON.parse(raw.toString()) } catch { return }
      if (msg.type === 'query' && typeof msg.text === 'string') {
        await handleQuery(ws as unknown as WebSocket, msg.text)
      }
    },
  },
})

console.log(`[bridge] Jarvis desktop bridge listening on http://localhost:${PORT}`)
console.log(`[bridge] Proxying chat to ${PROXY_URL} with model "${MODEL}"`)
