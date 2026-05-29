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
//   POST   /api/kiosk { state:"on", monitor:<int> } → { ok, state, monitor }
//   POST   /api/kiosk { state:"off" }              → { ok, state }
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
  handleExtBrowse,
  registerExtensionWS,
  unregisterExtensionWS,
  isExtensionConnected,
  resolveExtensionResponse,
} from './ext_browse'
import {
  deleteSessionsBetween,
  listSessions,
  saveTurn,
} from './storage.js'
import { randomUUID, timingSafeEqual } from 'node:crypto'
import { execSync } from 'node:child_process'
import { AccessToken } from 'livekit-server-sdk'

const PORT       = parseInt(process.env.JARVIS_BRIDGE_PORT ?? '8765')
const HOSTNAME   = process.env.JARVIS_BRIDGE_HOST ?? '127.0.0.1'
const PROXY_URL  = process.env.JARVIS_PROXY_URL ?? 'http://localhost:4000'

// Bearer-token gate. Auth is REQUIRED by default (fail-closed) unless
// JARVIS_BRIDGE_INSECURE=1 is set for local dev without a token.
// The legacy JARVIS_REQUIRE_LOCAL_AUTH=1 env var is honoured for
// back-compat (the desktop launcher sets it; with the new default it is
// now redundant but harmless).
//
// Token value lives in ~/.jarvis/local-api-token.env (chmod 600), loaded
// by the launcher into JARVIS_LOCAL_API_TOKEN. If auth is required but no
// token is configured the bridge logs a loud warning at startup — it does
// NOT crash, so the service keeps running, but every non-public request
// will return 401 until the token is set or JARVIS_BRIDGE_INSECURE=1.
const REQUIRE_AUTH = process.env.JARVIS_BRIDGE_INSECURE !== '1'
const LOCAL_TOKEN  = process.env.JARVIS_LOCAL_API_TOKEN ?? ''

if (REQUIRE_AUTH && !LOCAL_TOKEN) {
  console.warn(
    '[bridge] WARNING: auth is required (fail-closed default) but ' +
    'JARVIS_LOCAL_API_TOKEN is not set. All non-public requests will ' +
    'be rejected with 401. Set the token in ~/.jarvis/local-api-token.env ' +
    'or set JARVIS_BRIDGE_INSECURE=1 to disable auth for local dev.'
  )
}
const PUBLIC_PATHS = new Set(['/health', '/api/ready', '/api/version', '/api/theme'])

// Allowlist of origins permitted to cross-origin call the bridge. The
// Tauri webview is `tauri://localhost`; the Chrome extension is
// `chrome-extension://<id>`. Drops the CORS=* wildcard that let any
// malicious web page hit /api/livekit/token to mint room JWTs (global
// review §P0-2). Set JARVIS_BRIDGE_CORS_ALLOW (comma-separated) to
// extend, e.g. to add the dev-server origin during development.
const CORS_ALLOWLIST = new Set<string>([
  'tauri://localhost',
  'app://localhost',
  'http://localhost:3000',  // Tauri dev server
  'http://127.0.0.1:3000',
  ...(process.env.JARVIS_BRIDGE_CORS_ALLOW ?? '').split(',').map(s => s.trim()).filter(Boolean),
])

function corsHeaders(req: Request, methods: string): Record<string, string> {
  // Echo back the Origin if it's allowlisted OR a chrome-extension://
  // scheme (which carries the extension's UUID and changes per install
  // so we can't pin it ahead of time). Anything else → no CORS header,
  // browser blocks the response.
  const origin = req.headers.get('Origin') ?? ''
  let allow = ''
  if (CORS_ALLOWLIST.has(origin)) {
    allow = origin
  } else if (origin.startsWith('chrome-extension://')) {
    allow = origin
  }
  const headers: Record<string, string> = {
    'Access-Control-Allow-Methods': methods,
    'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    'Vary': 'Origin',
  }
  if (allow) headers['Access-Control-Allow-Origin'] = allow
  return headers
}

// Constant-time token comparison. Returns false if either argument is
// empty or the lengths differ (guards against timing side-channels and
// the empty-token fail-open bug: tokenEq('', '') === false).
function tokenEq(a: string, b: string): boolean {
  if (!a || !b || a.length !== b.length) return false
  return timingSafeEqual(Buffer.from(a), Buffer.from(b))
}

function isAuthorized(req: Request, urlObj: URL): boolean {
  if (!REQUIRE_AUTH) return true
  if (PUBLIC_PATHS.has(urlObj.pathname)) return true
  if (urlObj.pathname === '/ws') {
    // WebSocket auth via ?token= query param (no headers on initial upgrade
    // for native WS clients) OR Sec-WebSocket-Protocol subprotocol value.
    const qp = urlObj.searchParams.get('token') ?? ''
    const subproto = req.headers.get('Sec-WebSocket-Protocol') ?? ''
    return tokenEq(qp, LOCAL_TOKEN) || tokenEq(subproto, LOCAL_TOKEN)
  }
  const auth = req.headers.get('Authorization') ?? ''
  const bearer = auth.startsWith('Bearer ') ? auth.slice(7) : ''
  return tokenEq(bearer, LOCAL_TOKEN)
}
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

// LiveKit JWT minting. Client (Tauri webview / Android app) hits
// /api/livekit/token to trade its identity for a short-lived token
// that authorises joining a specific SFU room. Secret never leaves
// this process — the client only ever sees the JWT. Env values come
// from voice-agent/.env which systemd includes via EnvironmentFile
// (or via cli/.env.local for ad-hoc bun invocations).
const LIVEKIT_URL        = process.env.LIVEKIT_URL        ?? 'ws://127.0.0.1:7880'
const LIVEKIT_API_KEY    = process.env.LIVEKIT_API_KEY    ?? ''
const LIVEKIT_API_SECRET = process.env.LIVEKIT_API_SECRET ?? ''

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
  // Keep a reference to the upstream reader so cancel() can release the
  // upstream socket when the SSE client disconnects mid-stream.
  let upstreamReader: ReadableStreamDefaultReader<Uint8Array> | undefined
  const out = new ReadableStream<Uint8Array>({
    async start(controller) {
      upstreamReader = upstream.body!.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      try {
        while (true) {
          const { done, value } = await upstreamReader.read()
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
    cancel() {
      // Client disconnected — cancel the upstream reader to release the
      // proxy HTTP socket immediately rather than draining the full response.
      upstreamReader?.cancel().catch(() => {})
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

/**
 * Mint a short-lived LiveKit JWT for the given identity + room. Grants
 * the caller microphone-publish + subscribe rights so they can hold a
 * conversation with the jarvis-voice-agent worker that's registered
 * against the same SFU.
 *
 * Why not put this on the agent side? The Python agent is a *worker*
 * that reacts to jobs, not a gateway. We need a stable HTTP endpoint
 * the frontend can call at any time — the bridge is that endpoint for
 * every other UI concern, so it's natural to host token minting here
 * too. Client flow:
 *   1. Tauri webview POSTs /api/livekit/token with {identity, room}
 *   2. Bridge signs a 1-hour JWT with LIVEKIT_API_SECRET
 *   3. Webview uses the JWT + LIVEKIT_URL to connect via livekit-client
 *   4. Server sees the client, spawns a job → the pre-warmed Python
 *      agent worker picks it up and joins the same room
 *
 * Identity is client-supplied because a single user might have multiple
 * simultaneous clients (desktop + phone). Room defaults to "jarvis" so
 * the agent always knows which room to listen on; override per-client
 * if we ever want separate contexts.
 */
async function handleLiveKitToken(req: Request): Promise<Response> {
  // CORS via allowlist (2026-05-16 §P0-2). Tauri webview + Chrome ext
  // can mint; arbitrary web pages cannot — closes the most severe
  // attack from the global review (any browsed page could previously
  // mint a JWT, join the room as "jarvis-agent", and speak commands).
  const cors = corsHeaders(req, 'POST, OPTIONS')
  if (!LIVEKIT_API_KEY || !LIVEKIT_API_SECRET) {
    return Response.json(
      { error: 'LIVEKIT_API_KEY / LIVEKIT_API_SECRET not configured on bridge' },
      { status: 503, headers: cors },
    )
  }
  let body: any
  try { body = await req.json() } catch { body = {} }
  const identity = (body?.identity ?? 'desktop-ulrich').toString().slice(0, 64)
  const room     = (body?.room     ?? 'jarvis'         ).toString().slice(0, 64)
  const token = new AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET, {
    identity,
    // TTL — one hour is enough for any realistic voice session; clients
    // can re-mint if they leave and re-join.
    ttl:  60 * 60,
  })
  token.addGrant({
    room,
    roomJoin:     true,
    canPublish:   true,
    canSubscribe: true,
    // Can publish plain audio; we don't need video for voice mode.
    canPublishData: true,
  })
  const jwt = await token.toJwt()
  return Response.json(
    { token: jwt, url: LIVEKIT_URL, room, identity },
    { headers: cors },
  )
}

// Safety guard: refuse to bind on a non-loopback interface unless the operator
// has explicitly opted in. The bridge already has bearer-token auth, but the
// guard keeps both servers consistent and defends against a misconfigured token
// or future auth regression. Set JARVIS_ALLOW_PUBLIC_BIND=1 to override.
const LOOPBACK_HOSTS_BRIDGE = new Set(['127.0.0.1', 'localhost', '::1'])
if (!LOOPBACK_HOSTS_BRIDGE.has(HOSTNAME) && process.env.JARVIS_ALLOW_PUBLIC_BIND !== '1') {
  console.error(
    `[bridge] refusing non-loopback bind "${HOSTNAME}" without JARVIS_ALLOW_PUBLIC_BIND=1 — ` +
    'set JARVIS_BRIDGE_HOST to a loopback address or set JARVIS_ALLOW_PUBLIC_BIND=1 to allow.',
  )
  process.exit(1)
}

const server = Bun.serve({
  port: PORT,
  hostname: HOSTNAME,
  async fetch(req, server) {
    const url = new URL(req.url)

    // CORS via allowlist (2026-05-16 §P0-2). Tauri webview at
    // `tauri://localhost`/`app://localhost` + Chrome ext at
    // `chrome-extension://<id>` echo back; arbitrary web pages do not.
    // Closes the previous `*` policy that let any malicious tab mint
    // LiveKit JWTs, model-swap, or query chat-history.
    const cors = {
      ...corsHeaders(req, 'GET, POST, DELETE, OPTIONS'),
      'Access-Control-Max-Age':       '86400',
    }
    // OPTIONS preflight MUST run before isAuthorized(): browsers send no
    // Authorization header on a preflight by spec, so gating it behind
    // auth would 401 every cross-origin call to /api/* endpoints from the
    // Tauri webview or Chrome extension. Preflights only reveal allowed
    // methods/headers, so responding 204 here is safe.
    if (req.method === 'OPTIONS' && url.pathname.startsWith('/api/')) {
      return new Response(null, { status: 204, headers: cors })
    }

    if (!isAuthorized(req, url)) {
      return new Response('Unauthorized', { status: 401 })
    }

    if (url.pathname === '/ws') {
      if (server.upgrade(req)) return
      return new Response('WebSocket upgrade failed', { status: 400 })
    }
    // Response.json returns with its own headers — we wrap it at the
    // end of each route's return. Simplest path: intercept the final
    // response once we've built it. We do this by wrapping dispatch
    // below and walking back up to add cors. But the least-invasive
    // change is: attach cors to the default response helper used by
    // the endpoints that need it. Only the new /api/livekit/token is
    // exercised by the webview's fetch today — for others, the
    // existing GET routes are CORS-safe without headers (simple
    // requests don't preflight). Apply cors to /api/livekit/token.

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

    if (url.pathname === '/api/kiosk' && req.method === 'POST') {
      let body: any
      try { body = await req.json() } catch {
        return Response.json({ error: 'invalid JSON' }, { status: 400 })
      }
      const state = typeof body?.state === 'string' ? body.state : ''
      if (!['on', 'off'].includes(state)) {
        return Response.json({ error: 'state must be on|off (no toggle in v2)' }, { status: 400 })
      }
      if (state === 'on') {
        const monitor = body?.monitor
        if (typeof monitor !== 'number' || !Number.isInteger(monitor) || monitor < 0) {
          return Response.json({ error: 'state=on requires monitor (non-negative integer)' }, { status: 400 })
        }
        broadcast({ type: 'kiosk', state: 'on', monitor })
        return Response.json({ ok: true, state: 'on', monitor })
      }
      // state === 'off'
      broadcast({ type: 'kiosk', state: 'off' })
      return Response.json({ ok: true, state: 'off' })
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
    if (url.pathname === '/api/livekit/token'  && req.method === 'POST') return handleLiveKitToken(req)
    if (url.pathname === '/api/ext_browse'     && req.method === 'POST') return handleExtBrowse(req)
    if (url.pathname === '/api/ext_status') {
      return Response.json({ connected: isExtensionConnected() })
    }

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
      unregisterExtensionWS(sock)
      console.log(`[bridge] client disconnected (${clients.size} total)`)
    },
    async message(ws, raw) {
      let msg: any
      try { msg = JSON.parse(raw.toString()) } catch { return }

      // Extension identification — register this WS as the extension channel.
      // The extension sends {type:'extension_hello'} as its first message so we
      // can discriminate it from regular chat-panel / desktop clients.
      //
      // Auth (2026-05-16 §P0-3): require the bearer token in the hello
      // payload when REQUIRE_AUTH is on. Without this, any local process
      // can send {type:'extension_hello'} and IMPERSONATE the extension —
      // the bridge would then forward every ext_browse call from the
      // voice agent to the impostor, exfiltrating cookies / localStorage /
      // session_state on every authenticated tab. The token is the same
      // one isAuthorized() uses for /api/* requests.
      if (msg.type === 'extension_hello') {
        if (REQUIRE_AUTH && !tokenEq(msg.token ?? '', LOCAL_TOKEN)) {
          console.warn('[bridge] extension_hello rejected — missing/invalid token')
          try { ws.send(JSON.stringify({ type: 'extension_hello_nack', reason: 'auth' })) } catch {}
          try { ws.close(1008, 'auth') } catch {}
          return
        }
        registerExtensionWS(ws as unknown as WebSocket)
        console.log('[bridge] extension WebSocket registered')
        try { ws.send(JSON.stringify({ type: 'extension_hello_ack' })) } catch {}
        return
      }

      // Same auth gate for client-initiated `query` messages — the WS
      // upgrade auth (token via ?token= or Sec-WebSocket-Protocol) only
      // gates the handshake; once connected, any sender could spam
      // /api/think-equivalent queries through the WS. Require the token
      // on every query (unconditional per-message auth). Echoes the
      // existing isAuthorized() contract for /api/* endpoints.
      if (REQUIRE_AUTH && msg.type === 'query' && !tokenEq(msg.token ?? '', LOCAL_TOKEN)) {
        console.warn('[bridge] query rejected — missing/invalid token')
        try { ws.send(JSON.stringify({ type: 'chat_response', text: '(auth required)' })) } catch {}
        return
      }

      // Extension command response — correlate by cmd_id and wake the waiting
      // handleExtBrowse() promise. These messages never reach the chat handlers.
      if (msg.cmd_id) {
        resolveExtensionResponse(msg)
        return
      }

      // Existing chat-panel / desktop WS handling (unchanged).
      if (msg.type === 'query' && typeof msg.text === 'string') {
        await handleQuery(ws as unknown as WebSocket, msg.text)
      } else if (msg.type === 'feedback') {
        console.log(`[bridge] feedback: score=${msg.score} comment="${msg.comment ?? ''}"`)
        try { ws.send(JSON.stringify({ type: 'feedback_ack' })) } catch {}
      }
    },
  },
})

console.log(`[bridge] Jarvis desktop bridge listening on http://${HOSTNAME}:${PORT} (commit ${COMMIT}, auth=${REQUIRE_AUTH ? 'required' : 'off'})`)
console.log(`[bridge] Proxying chat to ${PROXY_URL} with active model "${ACTIVE_MODEL}"`)
