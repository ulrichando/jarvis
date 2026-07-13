import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import {
  archiveSession,
  unarchiveSession,
  deleteSession,
  findEnvironment,
  findSession,
  setSessionAutofix,
  setSessionAutomerge,
  setSessionDispatch,
  setSessionGroup,
  setSessionPinned,
  setSessionRead,
  setSessionTitle,
} from '@/lib/bridge/store'
import { extractBearer } from '@/lib/bridge/auth'
import { getUserId } from '@/lib/auth-helpers'
import { bridgeError } from '@/lib/bridge/errors'
import { ccrSessionStatus } from '@/lib/bridge/ccrCompat'
import { acquireKeepAwake, releaseKeepAwake } from '@/lib/dispatch/keep-awake'

// Authorize a mutation on a session two ways: the CLI worker presents a
// bearer (v1-permissive — any non-empty token); the /code browser presents a
// same-origin session cookie, checked against the session's owning
// environment (mirrors the messages route). Returns an error response, or
// null when allowed.
async function authorizeMutation(
  req: Request,
  sessionId: string,
): Promise<NextResponse | null> {
  if (extractBearer(req.headers.get('authorization'))) return null
  const store = getStore()
  const session = findSession(store, sessionId)
  if (!session) return bridgeError(404, 'not_found', 'Session not found')
  const env = session.environment_id
    ? findEnvironment(store, session.environment_id)
    : null
  const userId = await getUserId(req.headers)
  if (env?.user_id && env.user_id !== userId) {
    // No valid session against a real-owned session → 401 (re-login), not a
    // dead-end 403. A real cross-user mismatch still 403s.
    if (userId === null) {
      return bridgeError(401, 'unauthenticated', 'Session expired — please sign in again')
    }
    return bridgeError(403, 'forbidden', 'Not your session')
  }
  return null
}

// GET /api/bridge/v1/sessions/{id} — single-session fetch, used by the CLI's
// reconnect paths (getBridgeSession). Returns the fields the CLI reads:
// environment_id (for --session-id resume) and title. `status` (additive,
// Phase C) is the ccrSessionStatus run state the gh-app worker polls for its
// done-signal (running/idle/requires_action/archived); `worker_reported`
// (additive, C1) tells the poller whether the container CLI worker has EVER
// PUT state — before that, `status:'running'` is only ccrSessionStatus's
// fall-through default and must not arm the poller's done-signal.
export async function GET(
  _req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params
  try {
    const store = getStore()
    const session = findSession(store, sessionId)
    if (!session) return bridgeError(404, 'not_found', 'Session not found')
    return NextResponse.json({
      id: session.session_id,
      environment_id: session.environment_id,
      title: session.title,
      archived: !!session.archived,
      created_at: session.created_at,
      status: ccrSessionStatus(session),
      worker_reported: !!session.worker_state_json,
    })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}

// PATCH /api/bridge/v1/sessions/{id} — { title } to rename (CLI's
// updateBridgeSessionTitle + the /code sidebar Rename) or { archived: true }
// to archive from the sidebar. Title is stored on the sessions.title column,
// NOT as a session_events row (which the session view would render as a bare
// "title" line). Bearer (CLI) or session-cookie (browser) authorized.
export async function PATCH(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params
  const body = (await req.json().catch(() => null)) as {
    title?: string
    archived?: boolean
    pinned?: boolean
    read?: boolean
    group_id?: string | null
    autofix?: boolean
    automerge?: boolean
    keep_awake?: boolean
    allow_all_actions?: boolean
  } | null
  const renaming = typeof body?.title === 'string' && body.title.trim() !== ''
  const archiving = body?.archived === true
  const unarchiving = body?.archived === false
  const pinning = typeof body?.pinned === 'boolean'
  const reading = typeof body?.read === 'boolean'
  const grouping = body !== null && 'group_id' in body
  const togglingAutofix = typeof body?.autofix === 'boolean'
  const togglingAutomerge = typeof body?.automerge === 'boolean'
  const togglingKeepAwake = typeof body?.keep_awake === 'boolean'
  const togglingAllowActions = typeof body?.allow_all_actions === 'boolean'
  if (
    !renaming &&
    !archiving &&
    !unarchiving &&
    !pinning &&
    !reading &&
    !grouping &&
    !togglingAutofix &&
    !togglingAutomerge &&
    !togglingKeepAwake &&
    !togglingAllowActions
  ) {
    return bridgeError(
      400,
      'invalid_request',
      'title, archived, pinned, read, group_id, autofix, automerge, keep_awake, or allow_all_actions required',
    )
  }
  const denied = await authorizeMutation(req, sessionId)
  if (denied) return denied
  try {
    const store = getStore()
    const session = findSession(store, sessionId)
    if (!session) return bridgeError(404, 'not_found', 'Session not found')
    if (renaming) setSessionTitle(store, sessionId, body!.title!.trim())
    if (pinning) setSessionPinned(store, sessionId, body!.pinned!)
    if (reading) setSessionRead(store, sessionId, body!.read!)
    if (grouping) {
      const g = body!.group_id
      setSessionGroup(store, sessionId, typeof g === 'string' && g ? g : null)
    }
    if (archiving) {
      archiveSession(store, sessionId)
      releaseKeepAwake(sessionId) // archived task must not hold the sleep lock
    }
    if (unarchiving) unarchiveSession(store, sessionId)
    if (togglingAutofix) setSessionAutofix(store, sessionId, body!.autofix!)
    if (togglingAutomerge) setSessionAutomerge(store, sessionId, body!.automerge!)
    if (togglingKeepAwake || togglingAllowActions) {
      setSessionDispatch(store, sessionId, {
        keep_awake: togglingKeepAwake ? body!.keep_awake! : undefined,
        allow_all_actions: togglingAllowActions ? body!.allow_all_actions! : undefined,
      })
      // Flipping keep-awake mid-run is symmetric: ON acquires the sleep
      // inhibitor (dedup no-op if already held), OFF drops it.
      if (togglingKeepAwake) {
        if (body!.keep_awake === true) acquireKeepAwake(sessionId)
        else releaseKeepAwake(sessionId)
      }
    }
    return NextResponse.json({ id: sessionId })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}

// DELETE /api/bridge/v1/sessions/{id} — permanently remove a session and its
// events (the /code sidebar Delete). Session-cookie authorized for the
// browser; a bearer also works for tooling.
export async function DELETE(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params
  const denied = await authorizeMutation(req, sessionId)
  if (denied) return denied
  try {
    deleteSession(getStore(), sessionId)
    releaseKeepAwake(sessionId) // deleted task must not hold the sleep lock
    return new NextResponse(null, { status: 204 })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
