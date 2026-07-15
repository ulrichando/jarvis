import { NextResponse } from 'next/server'
import { randomBytes, randomUUID } from 'node:crypto'
import { getStore } from '@/lib/bridge/db'
import {
  findEnvironment,
  getOrCreateSession,
  setSessionDispatch,
  appendSessionEvent,
  appendInbound,
} from '@/lib/bridge/store'
import { getUserIdOrSharedLocal } from '@/lib/auth-helpers'
import { apiBaseFromRequest, dispatchSessionWork } from '@/lib/bridge/dispatch'
import { launchContainerSession } from '@/lib/bridge/containers'
import { bridgeError } from '@/lib/bridge/errors'
import { acquireKeepAwake, releaseKeepAwake } from '@/lib/dispatch/keep-awake'

// POST /api/bridge/v1/dispatch — the phone-first "Dispatch" composer. Identical
// pipeline to POST /tasks (register a session on the chosen machine, seed the
// prompt, enqueue {type:'session'} work), with three differences: it marks the
// session dispatch=1 (badged in the /code sidebar), persists the two Dispatch
// toggles (keep_awake, allow_all_actions), and DERIVES the permission mode from
// allow_all_actions rather than trusting a client-sent mode — a phone-initiated
// task defaults to 'default' (ask/approve), and only 'bypassPermissions' when
// the user explicitly flips "Allow all actions" on. Reuses dispatchSessionWork /
// launchContainerSession verbatim — no new work type, no worker/CLI change.
export async function POST(req: Request): Promise<NextResponse> {
  const body = (await req.json().catch(() => null)) as {
    environment_id?: string
    prompt?: string
    keep_awake?: boolean
    allow_all_actions?: boolean
    model?: string
    images?: Array<{ media_type?: string; data?: string }>
    extra_repos?: string[]
    connectors?: string[]
  } | null
  if (
    !body ||
    typeof body.environment_id !== 'string' ||
    typeof body.prompt !== 'string' ||
    !body.prompt.trim()
  ) {
    return bridgeError(
      400,
      'invalid_request',
      'environment_id and a non-empty prompt are required',
    )
  }
  const prompt = body.prompt.trim()
  const keepAwake = body.keep_awake === true
  const allowAllActions = body.allow_all_actions === true
  // Security default: a phone-initiated task on the local box + bypassPermissions
  // widens the blast radius, so default to ask/approve unless the user opts in.
  const permissionMode = allowAllActions ? 'bypassPermissions' : 'default'
  let sessionId = ''
  let workId = ''
  try {
    const store = getStore()
    const env = findEnvironment(store, body.environment_id)
    if (!env) {
      return bridgeError(404, 'not_found', 'Environment not found')
    }
    // Ownership: you can only dispatch to your own machines.
    const userId = await getUserIdOrSharedLocal(req.headers)
    if (env.user_id && env.user_id !== userId) {
      if (userId === null) {
        return bridgeError(401, 'unauthenticated', 'Session expired — please sign in again')
      }
      return bridgeError(403, 'forbidden', 'Not your machine')
    }
    sessionId = randomBytes(8).toString('hex')
    getOrCreateSession(store, sessionId, body.environment_id)
    setSessionDispatch(store, sessionId, {
      dispatch: true,
      keep_awake: keepAwake,
      allow_all_actions: allowAllActions,
    })
    // Keep-awake enforcement: hold a systemd-inhibit sleep lock on this box
    // while the task runs (web server is co-located with the desktop worker).
    // Fire-and-forget — acquireKeepAwake never throws and never blocks; it
    // no-ops on hosts without systemd-inhibit (e.g. a VPS deploy). Released
    // on done (push fire), interrupt, archive, or delete.
    if (keepAwake) acquireKeepAwake(sessionId)
    // Surface the user's prompt immediately as the first event, before any
    // worker has claimed the work — so the session view isn't empty.
    const uuid = randomUUID()
    appendSessionEvent(store, sessionId, {
      type: 'user_prompt',
      payload: { type: 'user_prompt', prompt, uuid },
    })
    // Seed the derived permission mode BEFORE the prompt — the child replays
    // inbound in sequence order, so the mode applies before work starts.
    const modeUuid = randomUUID()
    appendInbound(store, sessionId, {
      type: 'control_request',
      uuid: modeUuid,
      request_id: modeUuid,
      request: { subtype: 'set_permission_mode', mode: permissionMode },
    })
    // Seed the chosen model BEFORE the prompt too.
    if (typeof body.model === 'string' && body.model.trim()) {
      const modelUuid = randomUUID()
      appendInbound(store, sessionId, {
        type: 'control_request',
        uuid: modelUuid,
        request_id: modelUuid,
        request: { subtype: 'set_model', model: body.model.trim() },
      })
    }
    // Seed the prompt (+ any image attachments) as the first inbound client
    // event. The spawned child replays the stream from seq 0 on connect, so
    // this is how the task prompt actually reaches the model.
    const images = (Array.isArray(body.images) ? body.images : [])
      .filter(
        (im): im is { media_type: string; data: string } =>
          !!im &&
          typeof im.media_type === 'string' &&
          im.media_type.startsWith('image/') &&
          typeof im.data === 'string' &&
          im.data.length > 0,
      )
      .slice(0, 10)
    const content: Array<Record<string, unknown>> = [{ type: 'text', text: prompt }]
    for (const im of images) {
      content.push({
        type: 'image',
        source: { type: 'base64', media_type: im.media_type, data: im.data },
      })
    }
    appendInbound(store, sessionId, {
      type: 'user',
      uuid,
      session_id: sessionId,
      parent_tool_use_id: null,
      message: { role: 'user', content },
    })
    // Container target: web is the worker manager (no bridge work queue).
    if (env.worker_type === 'container') {
      const repo = (env.git_repo_url ?? '')
        .replace(/^https:\/\/github\.com\//, '')
        .replace(/\.git$/, '')
      if (!repo) {
        // Early error return bypasses the route's catch — don't leak the lock.
        releaseKeepAwake(sessionId)
        return bridgeError(500, 'internal_error', 'Container target has no repo URL')
      }
      const origin = new URL(req.url).origin
      void launchContainerSession(store, {
        sessionId,
        repoFullName: repo,
        baseUrl: origin,
        model: typeof body.model === 'string' && body.model.trim() ? body.model.trim() : undefined,
        extraRepos: Array.isArray(body.extra_repos)
          ? body.extra_repos.filter((r): r is string => typeof r === 'string')
          : undefined,
        connectors: Array.isArray(body.connectors)
          ? body.connectors.filter((c): c is string => typeof c === 'string')
          : undefined,
      }).catch(() => {
        // Failure already emitted as a ✗ status event + container reaped.
        // A dead launch must not hold the sleep lock (no-op when unheld).
        releaseKeepAwake(sessionId)
      })
      return NextResponse.json({ session_id: sessionId }, { status: 200 })
    }
    const dispatched = dispatchSessionWork(
      store,
      body.environment_id,
      sessionId,
      apiBaseFromRequest(req),
    )
    workId = dispatched.work_id
  } catch (err) {
    // Failed dispatch must not hold the sleep lock (harmless no-op if unheld).
    if (sessionId) releaseKeepAwake(sessionId)
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
  return NextResponse.json({ session_id: sessionId, work_id: workId }, { status: 200 })
}
