/**
 * /api/computer-use — the web front door for the `/computer-use` feature.
 *
 * Auth: inherited, no token wiring. The page is login-gated (proxy.ts page
 * gate); same-origin fetch/EventSource from it passes the /api/* gate via the
 * `Sec-Fetch-Site: same-origin` carve-out in proxy.ts. Non-browser callers
 * still need the bearer.
 *
 *   GET  → status probe: is the desktop stream (websockify :6080) + the
 *          computer-use sidecar (:8771) up? Returns the noVNC WS url + the
 *          VNC password (read from ~/.jarvis/computer-use-vnc.pass, minted by
 *          bin/jarvis-computer-use-stream) so the logged-in page can connect
 *          @novnc/novnc. The password is an 8-char SECOND layer only — the real
 *          guards are x11vnc's -localhost bind, the web login gate, and (remote)
 *          the tunnel. Handing it to an already-authenticated same-origin page
 *          is fine.
 *   POST {task} → opens the agent loop: proxies the sidecar's SSE
 *          (:8771/run) straight through to the browser. The live desktop is
 *          shown separately by the noVNC stream, so this carries only the
 *          loop's text + action frames.
 */
import { promises as fs } from 'node:fs'
import os from 'node:os'
import path from 'node:path'

export const dynamic = 'force-dynamic'
export const runtime = 'nodejs'

const SIDECAR = process.env.JARVIS_COMPUTER_USE_WEB_URL ?? 'http://127.0.0.1:8771'
const VNC_WS_PORT = process.env.JARVIS_CU_WS_PORT ?? '6080'
// Default to loopback for local use. For remote (domain + tunnel) the operator
// points this at the tunnelled VNC route, e.g. wss://jarvis.example.com/vnc.
const VNC_WS_URL =
  process.env.JARVIS_CU_VNC_WS_URL ?? `ws://127.0.0.1:${VNC_WS_PORT}`
const PASS_FILE = path.join(os.homedir(), '.jarvis', 'computer-use-vnc.pass')

const SSE_HEADERS = {
  'Content-Type': 'text/event-stream',
  'Cache-Control': 'no-cache, no-transform',
  Connection: 'keep-alive',
  'X-Accel-Buffering': 'no',
} as const

async function readVncPassword(): Promise<string | null> {
  // Split topology: the VNC password is set in the computer-use container, not
  // on the web container's disk — prefer the env var, fall back to the local
  // file for co-located / local dev.
  const fromEnv = process.env.JARVIS_CU_VNC_PASSWORD?.trim()
  if (fromEnv) return fromEnv
  try {
    const raw = await fs.readFile(PASS_FILE, 'utf8')
    const pass = raw.trim()
    return pass || null
  } catch {
    return null // not minted yet → stream hasn't been started
  }
}

type Health = { ok: boolean; providers?: Record<string, boolean>; streamUp?: boolean }

async function sidecarHealth(): Promise<Health | null> {
  try {
    const r = await fetch(`${SIDECAR}/health`, { signal: AbortSignal.timeout(800) })
    if (!r.ok) return null
    return (await r.json()) as Health
  } catch {
    return null
  }
}

export async function GET(): Promise<Response> {
  const [health, password] = await Promise.all([sidecarHealth(), readVncPassword()])
  const scUp = !!health?.ok
  // Stream liveness comes from the sidecar's /health (co-located with websockify)
  // rather than a cross-container TCP probe from the web container.
  const streamUp = !!health?.streamUp
  const ready = streamUp && scUp && !!password
  return Response.json({
    ready,
    streamUp,
    sidecarUp: scUp,
    providers: health?.providers ?? {},
    wsUrl: VNC_WS_URL,
    // Only hand the password over once the stream is actually up.
    password: streamUp ? password : null,
    hint: ready ? null : 'Run `bin/jarvis-computer-use start` (local) or bring up the computer-use container (deployed).',
  })
}

function sseError(message: string): Response {
  const body = `data: ${JSON.stringify({ type: 'error', error: message })}\n\n`
  return new Response(body, { headers: SSE_HEADERS })
}

export async function POST(req: Request): Promise<Response> {
  let task = ''
  let sessionId = 'default'
  let supervised = true
  let model: string | undefined
  try {
    const body = (await req.json()) as {
      task?: unknown
      session_id?: unknown
      supervised?: unknown
      model?: unknown
    }
    task = typeof body?.task === 'string' ? body.task.trim() : ''
    if (typeof body?.session_id === 'string' && body.session_id) sessionId = body.session_id
    if (typeof body?.supervised === 'boolean') supervised = body.supervised
    if (typeof body?.model === 'string' && body.model) model = body.model
  } catch {
    /* empty/invalid body → task stays '' */
  }
  if (!task) return sseError('task required')

  let upstream: Response
  try {
    upstream = await fetch(`${SIDECAR}/run`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      // detach: the run survives the browser leaving (this fetch aborting via
      // req.signal only drops the subscription); the page reattaches via
      // /sessions + /events. The CLI omits the flag and keeps Ctrl-C-kills-run.
      body: JSON.stringify({ task, session_id: sessionId, supervised, model, detach: true }),
      signal: req.signal,
    })
  } catch {
    return sseError(
      'Computer-use sidecar is not running on :8771 — run `bin/jarvis-computer-use start`.',
    )
  }
  if (!upstream.body) return sseError('sidecar returned no stream')

  // Pipe the sidecar's SSE straight through (it already emits `data: {...}` frames).
  return new Response(upstream.body, { status: upstream.status, headers: SSE_HEADERS })
}
