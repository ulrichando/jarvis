import { NextResponse } from 'next/server'
import { randomBytes } from 'node:crypto'
import { getStore } from '@/lib/bridge/db'
import {
  appendSessionEvent,
  findEnvironment,
  getOrCreateSession,
  listEnvironments,
  listSessions,
  findSession,
  resolveBridgeToken,
} from '@/lib/bridge/store'
import { extractBearer } from '@/lib/bridge/auth'
import { apiBaseFromRequest, dispatchSessionWork } from '@/lib/bridge/dispatch'
import { bridgeError } from '@/lib/bridge/errors'
import { ccrSessionStatus } from '@/lib/bridge/ccrCompat'

// CCR-compat session create/list — see ./environment_providers/route.ts header.

type SessionContext = Record<string, unknown>

// POST /api/v1/sessions — the teleport/ultraplan client creates a remote
// session. Body: { title?, events?, session_context?, environment_id? }. The
// initial `events` carry the set_permission_mode control_request (incl.
// ultraplan); they're persisted before the worker connects so plan mode is
// applied before the first turn (cli/print.ts:2918). Reuses the existing
// create+dispatch path; returns the CCR SessionResource shape.
export async function POST(req: Request): Promise<NextResponse> {
  const token = extractBearer(req.headers.get('authorization'))
  if (!token) return bridgeError(401, 'unauthorized', 'Missing bearer')
  const body = (await req.json().catch(() => null)) as {
    title?: string
    environment_id?: string
    session_context?: SessionContext
    events?: Array<{ type: string; [k: string]: unknown }>
  } | null
  if (!body) return bridgeError(400, 'invalid_request', 'JSON body required')
  try {
    const store = getStore()
    const userId = resolveBridgeToken(store, token)
    // Resolve the target environment: explicit id, else the caller's first
    // registered (online-preferred) bridge environment. Ultraplan needs a
    // polling bridge worker, so we target a registered machine rather than
    // synthesizing a cloud container.
    let envId = body.environment_id ?? null
    if (envId && !findEnvironment(store, envId)) {
      return bridgeError(404, 'not_found', 'Environment not found')
    }
    if (!envId) {
      const envs = listEnvironments(store, userId ?? undefined)
      envId = envs[0]?.environment_id ?? null
    }
    if (!envId) {
      return bridgeError(
        409,
        'no_environment',
        'No registered environment — start a bridge worker (bin/jarvis)',
      )
    }
    const env = findEnvironment(store, envId)
    if (userId && env?.user_id && userId !== env.user_id) {
      return bridgeError(403, 'forbidden', 'Not your machine')
    }
    const sessionId = randomBytes(8).toString('hex')
    const title =
      typeof body.title === 'string' && body.title.trim()
        ? body.title.trim()
        : null
    getOrCreateSession(store, sessionId, envId, title)
    if (Array.isArray(body.events)) {
      for (const event of body.events) {
        if (typeof event?.type !== 'string') continue
        appendSessionEvent(store, sessionId, { type: event.type, payload: event })
      }
    }
    dispatchSessionWork(store, envId, sessionId, apiBaseFromRequest(req))
    const now = new Date().toISOString()
    return NextResponse.json(
      {
        type: 'session',
        id: sessionId,
        title,
        session_status: ccrSessionStatus(findSession(store, sessionId)),
        environment_id: envId,
        created_at: now,
        updated_at: now,
        session_context: body.session_context ?? {},
      },
      { status: 201 },
    )
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}

// GET /api/v1/sessions — CCR-compat list (the client's listRemoteSessions).
export async function GET(req: Request): Promise<NextResponse> {
  const token = extractBearer(req.headers.get('authorization'))
  const store = getStore()
  const userId = token ? resolveBridgeToken(store, token) : null
  const data = listSessions(store, userId ?? undefined).map((s) => ({
    type: 'session' as const,
    id: s.session_id,
    title: s.title,
    session_status: ccrSessionStatus(s),
    environment_id: s.environment_id,
    created_at: new Date(s.created_at).toISOString(),
    updated_at: new Date(s.created_at).toISOString(),
  }))
  return NextResponse.json({
    data,
    has_more: false,
    first_id: data[0]?.id ?? null,
    last_id: data[data.length - 1]?.id ?? null,
  })
}
