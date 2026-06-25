import { NextResponse } from 'next/server'
import { randomBytes } from 'node:crypto'
import { getStore } from '@/lib/bridge/db'
import {
  listSessions,
  listSessionEvents,
  listGroups,
  findEnvironment,
  getOrCreateSession,
  appendSessionEvent,
  resolveBridgeToken,
  type EnvironmentRow,
} from '@/lib/bridge/store'
import { getUserId } from '@/lib/auth-helpers'
import { extractBearer } from '@/lib/bridge/auth'
import { apiBaseFromRequest, dispatchSessionWork } from '@/lib/bridge/dispatch'
import { bridgeError } from '@/lib/bridge/errors'

function repoLabel(env: EnvironmentRow | null): string | null {
  if (!env) return null
  if (env.git_repo_url) {
    const s = env.git_repo_url.replace(/\.git$/, '').split('/')
    return s.slice(-2).join('/') || (s.slice(-1)[0] ?? null)
  }
  return env.directory.split('/').filter(Boolean).slice(-1)[0] ?? null
}

// GET /api/bridge/v1/sessions — sessions for the /code main view, each with a
// title (first user prompt), a preview (latest event), repo + machine, and a
// derived status. Newest first, capped.
export async function GET(req: Request): Promise<NextResponse> {
  try {
    const store = getStore()
    const userId = await getUserId(req.headers)
    if (!userId) return bridgeError(401, 'unauthenticated', 'Sign in required')
    const groupName = new Map(listGroups(store, userId).map((g) => [g.group_id, g.name]))
    const sessions = listSessions(store, userId)
      .slice(0, 40)
      .map((s) => {
        const events = listSessionEvents(store, s.session_id, 0)
        const first = events.find((e) => e.type === 'user_prompt')
        const last = events[events.length - 1]
        const env = s.environment_id ? findEnvironment(store, s.environment_id) : null
        const safe = (json: string | undefined, key: string): string => {
          try {
            const v = (JSON.parse(json ?? '{}') as Record<string, unknown>)[key]
            return typeof v === 'string' ? v : ''
          } catch {
            return ''
          }
        }
        const title =
          s.title || safe(first?.payload_json, 'prompt') || 'Session'
        const preview =
          safe(last?.payload_json, 'text') ||
          safe(last?.payload_json, 'status') ||
          safe(last?.payload_json, 'message')
        // Prefer the worker's own reported status (PUT /worker) over the
        // last-event heuristic. Parity with claude.ai/code: 'needs_input'
        // (amber) is reserved for a worker actually BLOCKED on a permission /
        // question (requires_action). A finished, idle turn is 'done' (neutral)
        // — "your turn to type", not an alert. Lumping idle into needs_input
        // made every run-once session sit permanently amber.
        let workerStatus = ''
        try {
          const ws = s.worker_state_json
            ? (JSON.parse(s.worker_state_json) as { worker_status?: string })
            : null
          workerStatus = typeof ws?.worker_status === 'string' ? ws.worker_status : ''
        } catch {
          workerStatus = ''
        }
        const status = s.archived
          ? 'done'
          : workerStatus === 'running'
            ? 'working'
            : workerStatus === 'requires_action'
              ? 'needs_input'
              : workerStatus === 'idle'
                ? 'done'
                : last && last.type !== 'user_prompt'
                  ? 'working'
                  : 'needs_input'
        return {
          session_id: s.session_id,
          // The session's environment id — lets the /code session header
          // "Edit environment" resolve the right env to configure (the
          // composer pickers only reflect NEW-session intent, not this one).
          environment_id: s.environment_id ?? null,
          title: title.slice(0, 90),
          preview: preview.slice(0, 110),
          repo: repoLabel(env),
          machine_name: env?.machine_name ?? null,
          created_at: s.created_at,
          status,
          pinned: !!s.pinned,
          read: !!s.read,
          archived: !!s.archived,
          group_id: s.group_id,
          group_name: s.group_id ? (groupName.get(s.group_id) ?? null) : null,
        }
      })
    return NextResponse.json({ sessions })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}

// POST /api/bridge/v1/sessions — the CLI registers a session here:
// `/remote-control` (REPL attach, createBridgeSession) and worker-spawned
// children. Was missing in Phase 1 (GET only), so the attach died after
// environment registration with a 405 and /code never listed the session.
// v1-permissive auth like the events route: any non-empty bearer; ownership
// enforced when the bearer resolves to a known user token. Returns { id } —
// the only field the CLI reads.
export async function POST(req: Request): Promise<NextResponse> {
  const token = extractBearer(req.headers.get('authorization'))
  if (!token) return bridgeError(401, 'unauthorized', 'Missing bearer')
  const body = (await req.json().catch(() => null)) as {
    environment_id?: string
    title?: string
    events?: Array<{ type: string; [k: string]: unknown }>
  } | null
  if (!body || typeof body.environment_id !== 'string') {
    return bridgeError(400, 'invalid_request', 'environment_id required')
  }
  try {
    const store = getStore()
    const env = findEnvironment(store, body.environment_id)
    if (!env) return bridgeError(404, 'not_found', 'Environment not found')
    const tokenUser = resolveBridgeToken(store, token)
    if (tokenUser && env.user_id && tokenUser !== env.user_id) {
      return bridgeError(403, 'forbidden', 'Not your machine')
    }
    const sessionId = randomBytes(8).toString('hex')
    const title =
      typeof body.title === 'string' && body.title.trim()
        ? body.title.trim()
        : null
    getOrCreateSession(store, sessionId, body.environment_id, title)
    if (Array.isArray(body.events)) {
      for (const event of body.events) {
        if (typeof event?.type !== 'string') continue
        appendSessionEvent(store, sessionId, { type: event.type, payload: event })
      }
    }
    // Hand the session live to the polling CLI: mint its ingress token and
    // dispatch `session` work. The CLI's poll loop picks it up, registers as
    // the CCR v2 worker (use_code_sessions), connects the SSE transport, and
    // flushes its transcript — which is what makes the session show up live
    // in /code and accept messages from the composer.
    dispatchSessionWork(
      store,
      body.environment_id,
      sessionId,
      apiBaseFromRequest(req),
    )
    return NextResponse.json({ id: sessionId }, { status: 201 })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
