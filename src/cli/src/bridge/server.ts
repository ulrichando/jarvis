// Jarvis Desktop Bridge Server
//
// Exposes the WebSocket + REST API that the Tauri desktop app and the
// Firefox/Chrome extensions expect, and routes conversations through the
// Jarvis proxy on port 4000.
//
// Endpoints:
//   GET    /health                       → { status: 'ok' }
//   GET    /api/ready                    → { status: 'ready', model }
//   GET    /api/version                  → { commit } for update-banner polling
//   GET    /api/theme                    → { primary, glow }
//   GET    /api/models                   → { models: [{name}], active }
//   POST   /api/model                    → { model } — sets active model
//   POST   /api/mute                     → { muted: boolean }
//   POST   /api/think                    → { response: string }  (WS-dead fallback)
//   POST   /api/page-query               → SSE stream of { type: 'text'|'done'|'error' }
//   POST   /api/analyze-screen           → { response, model } — vision LLM
//   GET    /api/conversations/sessions   → { sessions: [...] }
//   DELETE /api/conversations/session    → { deleted: N }
//   GET    /ws?client=desktop            → WebSocket for chat + status events
//
// WS message shapes:
//   Client → server: { type: 'query',    text: string }
//                    { type: 'feedback', score: number, comment?: string }
//   Server → client: { type: 'status',   status: 'thinking' | 'idle' }
//                    { type: 'chat_response', text: string }
//                    { type: 'brain_ready' }
//                    { type: 'voice_muted', muted: boolean }
//                    { type: 'feedback_ack' }

import {
  getJarvisDefaultModel,
  getJarvisPickerModels,
} from '../utils/model/jarvisModelRegistry.js'
import {
  deleteSessionsBetween,
  listSessions,
  saveTurn,
} from './storage.js'
import { randomUUID } from 'node:crypto'
import { execSync } from 'node:child_process'

const PORT       = parseInt(process.env.JARVIS_BRIDGE_PORT ?? '8765')
const PROXY_URL  = process.env.JARVIS_PROXY_URL ?? 'http://localhost:4000'
// Mutable: /api/model lets the extension (and chat panel) pick a different
// model at runtime. Applies to every subsequent query that doesn't override.
let ACTIVE_MODEL = process.env.JARVIS_BRIDGE_MODEL ?? getJarvisDefaultModel().id
const THEME_PRIMARY = process.env.JARVIS_THEME_PRIMARY ?? '#67e8f9'
const THEME_GLOW    = process.env.JARVIS_THEME_GLOW    ?? '#a5f3fc'

// Vision path bypasses the proxy because convert.ts drops image blocks.
// Groq's Llama 4 Scout is multimodal; swap via env if you point this at
// another OpenAI-compatible vision endpoint.
const GROQ_KEY       = process.env.GROQ_API_KEY ?? ''
const VISION_MODEL   = process.env.JARVIS_VISION_MODEL ?? 'meta-llama/llama-4-scout-17b-16e-instruct'
const VISION_BASEURL = process.env.JARVIS_VISION_BASEURL ?? 'https://api.groq.com/openai/v1'

let COMMIT = 'unknown'
try {
  COMMIT = execSync('git rev-parse --short HEAD', {
    cwd: import.meta.dir,
    stdio: ['ignore', 'pipe', 'ignore'],
  }).toString().trim()
} catch { /* not a git checkout */ }

const clients = new Set<WebSocket>()
// Fresh session_id per WS connection — one chat-panel "open" = one session.
const wsSessionId = new WeakMap<WebSocket, string>()
let muted = false

function broadcast(msg: unknown): void {
  const data = JSON.stringify(msg)
  for (const ws of clients) {
    try { ws.send(data) } catch { /* closed */ }
  }
}

async function askLLM(text: string, model: string = ACTIVE_MODEL): Promise<string> {
  const resp = await fetch(`${PROXY_URL}/v1/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model,
      max_tokens: 4000,
      stream: false,
      messages: [{ role: 'user', content: text }],
      system: 'You are Jarvis, a helpful AI assistant. Keep responses concise.',
    }),
  })
  const data = await resp.json() as any
  return data?.content?.[0]?.text ?? '(no response)'
}

async function handleQuery(ws: WebSocket, text: string): Promise<void> {
  broadcast({ type: 'status', status: 'thinking' })
  const sessionId = wsSessionId.get(ws)
  if (sessionId) saveTurn(sessionId, 'user', text)
  try {
    const reply = await askLLM(text)
    if (sessionId) saveTurn(sessionId, 'assistant', reply)
    ws.send(JSON.stringify({ type: 'chat_response', text: reply }))
  } catch (e: any) {
    ws.send(JSON.stringify({ type: 'chat_response', text: `Error: ${e.message}` }))
  } finally {
    broadcast({ type: 'status', status: 'idle' })
  }
}

// ── /api/page-query: SSE stream ──────────────────────────────────────────
// Extension expects events shaped as {type:'text', content:'...'} chunks,
// then a final {type:'done'}. We wrap the Anthropic SSE stream the proxy
// emits and strip it down to that subset.
// The extension sends pageContent as a string OR a DOM-extraction object
// ({url, title, headings, text, …}). Normalize both into a readable block.
function stringifyPage(p: unknown): string {
  if (!p) return ''
  if (typeof p === 'string') return p.trim()
  if (typeof p === 'object') {
    const o = p as Record<string, any>
    const lines: string[] = []
    if (o.title) lines.push(`Title: ${o.title}`)
    if (o.url)   lines.push(`URL: ${o.url}`)
    if (Array.isArray(o.headings) && o.headings.length) {
      lines.push('Headings:')
      for (const h of o.headings.slice(0, 20)) {
        const level = Number(h?.level) || 1
        lines.push(`${'  '.repeat(Math.max(0, level - 1))}- ${h?.text ?? ''}`)
      }
    }
    if (typeof o.text === 'string' && o.text.trim()) {
      lines.push('', o.text.trim().slice(0, 8000))
    } else if (typeof o.content === 'string' && o.content.trim()) {
      lines.push('', o.content.trim().slice(0, 8000))
    }
    return lines.join('\n').trim()
  }
  return ''
}

function buildPagePrompt(
  query: string,
  pageContent?: unknown,
  mentionedTabs?: Array<Record<string, any>>,
): string {
  const parts: string[] = []
  const pageStr = stringifyPage(pageContent)
  if (pageStr) parts.push('## Current page\n' + pageStr)
  if (mentionedTabs?.length) {
    for (const tab of mentionedTabs) {
      const tabStr = stringifyPage(tab)
      if (tabStr) parts.push('## Mentioned tab\n' + tabStr)
    }
  }
  parts.push('## Question\n' + query)
  return parts.join('\n\n')
}

async function handlePageQuery(req: Request): Promise<Response> {
  let body: any
  try { body = await req.json() } catch {
    return Response.json({ error: 'invalid JSON' }, { status: 400 })
  }
  const query = typeof body?.query === 'string' ? body.query.trim() : ''
  if (!query) return Response.json({ error: 'query required' }, { status: 400 })

  const prompt = buildPagePrompt(query, body.pageContent, body.mentionedTabs)
  const model  = typeof body.model === 'string' && body.model ? body.model : ACTIVE_MODEL

  const upstream = await fetch(`${PROXY_URL}/v1/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      model,
      max_tokens: 4000,
      stream: true,
      messages: [{ role: 'user', content: prompt }],
      system: 'You are Jarvis, a helpful AI assistant. Respond concisely and refer to the provided page context when relevant.',
    }),
  })

  if (!upstream.ok || !upstream.body) {
    const msg = `upstream ${upstream.status}: ${await upstream.text()}`
    return sseError(msg)
  }

  const enc = new TextEncoder()
  const out = new ReadableStream<Uint8Array>({
    async start(controller) {
      const reader = upstream.body!.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      try {
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          buf += decoder.decode(value, { stream: true })
          const lines = buf.split('\n')
          buf = lines.pop() ?? ''
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue
            let ev: any
            try { ev = JSON.parse(line.slice(6)) } catch { continue }
            // Anthropic SSE: content_block_delta carries text_delta chunks
            if (ev.type === 'content_block_delta' && ev.delta?.type === 'text_delta') {
              const text = ev.delta.text ?? ''
              if (text) controller.enqueue(enc.encode(`data: ${JSON.stringify({ type: 'text', content: text })}\n\n`))
            }
          }
        }
        controller.enqueue(enc.encode(`data: ${JSON.stringify({ type: 'done' })}\n\n`))
      } catch (e: any) {
        controller.enqueue(enc.encode(`data: ${JSON.stringify({ type: 'error', content: e.message })}\n\n`))
      } finally {
        controller.close()
      }
    },
  })
  return new Response(out, {
    headers: {
      'Content-Type':  'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection':    'keep-alive',
    },
  })
}

function sseError(message: string): Response {
  const enc = new TextEncoder()
  return new Response(
    new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(enc.encode(`data: ${JSON.stringify({ type: 'error', content: message })}\n\n`))
        c.enqueue(enc.encode(`data: ${JSON.stringify({ type: 'done' })}\n\n`))
        c.close()
      },
    }),
    { headers: { 'Content-Type': 'text/event-stream' } },
  )
}

// ── /api/analyze-screen: vision ──────────────────────────────────────────
async function handleAnalyzeScreen(req: Request): Promise<Response> {
  if (!GROQ_KEY) {
    return Response.json({ error: 'GROQ_API_KEY not set — vision disabled' }, { status: 503 })
  }
  let body: any
  try { body = await req.json() } catch {
    return Response.json({ error: 'invalid JSON' }, { status: 400 })
  }
  const image = typeof body?.image === 'string' ? body.image : ''
  const query = typeof body?.query === 'string' ? body.query : 'Describe what you see.'
  if (!image.startsWith('data:image/')) {
    return Response.json({ error: 'image must be a data URL' }, { status: 400 })
  }

  try {
    const resp = await fetch(`${VISION_BASEURL}/chat/completions`, {
      method: 'POST',
      headers: {
        'Content-Type':  'application/json',
        'Authorization': `Bearer ${GROQ_KEY}`,
      },
      body: JSON.stringify({
        model: VISION_MODEL,
        max_tokens: 2000,
        messages: [{
          role: 'user',
          content: [
            { type: 'text', text: query },
            { type: 'image_url', image_url: { url: image } },
          ],
        }],
      }),
    })
    if (!resp.ok) {
      return Response.json({ error: `vision upstream ${resp.status}: ${await resp.text()}` }, { status: 502 })
    }
    const data = await resp.json() as any
    const response = data?.choices?.[0]?.message?.content ?? '(no response)'
    return Response.json({ response, model: VISION_MODEL })
  } catch (e: any) {
    return Response.json({ error: e.message }, { status: 500 })
  }
}

const server = Bun.serve({
  port: PORT,
  async fetch(req, server) {
    const url = new URL(req.url)

    if (url.pathname === '/ws') {
      if (server.upgrade(req)) return
      return new Response('WebSocket upgrade failed', { status: 400 })
    }

    if (url.pathname === '/health')      return Response.json({ status: 'ok' })
    if (url.pathname === '/api/ready')   return Response.json({ status: 'ready', model: ACTIVE_MODEL })
    if (url.pathname === '/api/version') return Response.json({ commit: COMMIT })
    if (url.pathname === '/api/theme')   return Response.json({ primary: THEME_PRIMARY, glow: THEME_GLOW })

    if (url.pathname === '/api/models' && req.method === 'GET') {
      return Response.json({
        models: getJarvisPickerModels().map(m => ({ name: m.id })),
        active: ACTIVE_MODEL,
      })
    }

    if (url.pathname === '/api/model' && req.method === 'POST') {
      let body: any
      try { body = await req.json() } catch {
        return Response.json({ error: 'invalid JSON' }, { status: 400 })
      }
      const model = typeof body?.model === 'string' ? body.model.trim() : ''
      if (!model) return Response.json({ error: 'model required' }, { status: 400 })
      ACTIVE_MODEL = model
      console.log(`[bridge] active model → ${ACTIVE_MODEL}`)
      return Response.json({ model: ACTIVE_MODEL })
    }

    if (url.pathname === '/api/mute' && req.method === 'POST') {
      muted = !muted
      broadcast({ type: 'voice_muted', muted })
      return Response.json({ muted })
    }

    if (url.pathname === '/api/think' && req.method === 'POST') {
      let body: any
      try { body = await req.json() } catch {
        return Response.json({ error: 'invalid JSON' }, { status: 400 })
      }
      const text = typeof body?.query === 'string' ? body.query.trim() : ''
      if (!text) return Response.json({ error: 'query required' }, { status: 400 })
      try {
        return Response.json({ response: await askLLM(text) })
      } catch (e: any) {
        return Response.json({ error: e.message }, { status: 500 })
      }
    }

    if (url.pathname === '/api/page-query'     && req.method === 'POST') return handlePageQuery(req)
    if (url.pathname === '/api/analyze-screen' && req.method === 'POST') return handleAnalyzeScreen(req)

    if (url.pathname === '/api/conversations/sessions' && req.method === 'GET') {
      return Response.json({ sessions: listSessions() })
    }

    if (url.pathname === '/api/conversations/session' && req.method === 'DELETE') {
      let body: any
      try { body = await req.json() } catch {
        return Response.json({ error: 'invalid JSON' }, { status: 400 })
      }
      const start = Number(body?.start_ts)
      const end   = Number(body?.end_ts)
      if (!Number.isFinite(start) || !Number.isFinite(end)) {
        return Response.json({ error: 'start_ts and end_ts required' }, { status: 400 })
      }
      return Response.json({ deleted: deleteSessionsBetween(start, end) })
    }

    return new Response('Not found', { status: 404 })
  },
  websocket: {
    open(ws) {
      const sock = ws as unknown as WebSocket
      clients.add(sock)
      wsSessionId.set(sock, randomUUID())
      console.log(`[bridge] client connected (${clients.size} total)`)
      ws.send(JSON.stringify({ type: 'brain_ready' }))
      ws.send(JSON.stringify({ type: 'status', status: 'idle' }))
    },
    close(ws) {
      const sock = ws as unknown as WebSocket
      clients.delete(sock)
      wsSessionId.delete(sock)
      console.log(`[bridge] client disconnected (${clients.size} total)`)
    },
    async message(ws, raw) {
      let msg: any
      try { msg = JSON.parse(raw.toString()) } catch { return }
      if (msg.type === 'query' && typeof msg.text === 'string') {
        await handleQuery(ws as unknown as WebSocket, msg.text)
      } else if (msg.type === 'feedback') {
        console.log(`[bridge] feedback: score=${msg.score} comment="${msg.comment ?? ''}"`)
        try { ws.send(JSON.stringify({ type: 'feedback_ack' })) } catch {}
      }
    },
  },
})

console.log(`[bridge] Jarvis desktop bridge listening on http://localhost:${PORT} (commit ${COMMIT})`)
console.log(`[bridge] Proxying chat to ${PROXY_URL} with active model "${ACTIVE_MODEL}"`)
