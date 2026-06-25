import { NextResponse } from 'next/server'
import { randomBytes, randomUUID } from 'node:crypto'
import { getStore } from '@/lib/bridge/db'
import {
  findEnvironment,
  getOrCreateSession,
  appendSessionEvent,
  appendInbound,
} from '@/lib/bridge/store'
import { getUserId } from '@/lib/auth-helpers'
import { apiBaseFromRequest, dispatchSessionWork } from '@/lib/bridge/dispatch'
import { launchContainerSession } from '@/lib/bridge/containers'
import { bridgeError } from '@/lib/bridge/errors'

// POST /api/bridge/v1/tasks — the /code UI dispatches a coding task: registers
// a session on the chosen environment (machine), seeds the prompt on the
// session's inbound stream, and enqueues `{type:'session'}` work with a CCR v2
// work secret. The polling CLI spawns a child for the session; the child's SSE
// catch-up (from_sequence_num=0) delivers the seeded prompt as its first user
// message. Returns session_id so the UI can tail its events.
//
// The previous shape — `{type:'prompt', …}` work with an empty secret — was
// dead on arrival twice over: decodeWorkSecret('') throws (work dropped), and
// even with a secret the CLI deliberately ignores unknown work types.
export async function POST(req: Request): Promise<NextResponse> {
  const body = (await req.json().catch(() => null)) as {
    environment_id?: string
    prompt?: string
    permission_mode?: string
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
  let sessionId = ''
  let workId = ''
  try {
    const store = getStore()
    const env = findEnvironment(store, body.environment_id)
    if (!env) {
      return bridgeError(404, 'not_found', 'Environment not found')
    }
    // Ownership: you can only dispatch to your own machines.
    const userId = await getUserId(req.headers)
    if (env.user_id && env.user_id !== userId) {
      // No valid session against an owned machine → 401 (re-login); a real
      // cross-user mismatch still 403s.
      if (userId === null) {
        return bridgeError(401, 'unauthenticated', 'Session expired — please sign in again')
      }
      return bridgeError(403, 'forbidden', 'Not your machine')
    }
    sessionId = randomBytes(8).toString('hex')
    getOrCreateSession(store, sessionId, body.environment_id)
    // Surface the user's prompt immediately as the first event, before any
    // worker has claimed the work — so the session view isn't empty.
    const uuid = randomUUID()
    appendSessionEvent(store, sessionId, {
      type: 'user_prompt',
      payload: { type: 'user_prompt', prompt, uuid },
    })
    // Seed the chosen permission mode BEFORE the prompt — the child replays
    // inbound in sequence order, so the mode applies before work starts.
    const VALID_MODES = ['default', 'acceptEdits', 'plan', 'bypassPermissions', 'dontAsk']
    if (
      typeof body.permission_mode === 'string' &&
      VALID_MODES.includes(body.permission_mode)
    ) {
      const modeUuid = randomUUID()
      appendInbound(store, sessionId, {
        type: 'control_request',
        uuid: modeUuid,
        request_id: modeUuid,
        request: { subtype: 'set_permission_mode', mode: body.permission_mode },
      })
    }
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
    // Container target (environments/cloud rows): the web is the worker
    // manager — no bridge work queue. Launch async; the init steps stream
    // into the session view as status events (container → clone → setup →
    // CLI), and the child picks the seeded prompt up via SSE catch-up
    // exactly like a bridge-spawned child.
    if (env.worker_type === 'container') {
      const repo = (env.git_repo_url ?? '')
        .replace(/^https:\/\/github\.com\//, '')
        .replace(/\.git$/, '')
      if (!repo) {
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
        // Per-session connector opt-in. The web UI always sends an array (empty
        // = none); a missing field falls through to undefined = all-enabled.
        connectors: Array.isArray(body.connectors)
          ? body.connectors.filter((c): c is string => typeof c === 'string')
          : undefined,
      }).catch(() => {
        /* failure already emitted as a ✗ status event + container reaped */
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
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
  return NextResponse.json({ session_id: sessionId, work_id: workId }, { status: 200 })
}
