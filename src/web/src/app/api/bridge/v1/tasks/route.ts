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
    // Seed the prompt as the first inbound client event. The spawned child
    // replays the stream from seq 0 on connect, so this is how the task
    // prompt actually reaches the model.
    appendInbound(store, sessionId, {
      type: 'user',
      uuid,
      session_id: sessionId,
      parent_tool_use_id: null,
      message: { role: 'user', content: [{ type: 'text', text: prompt }] },
    })
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
