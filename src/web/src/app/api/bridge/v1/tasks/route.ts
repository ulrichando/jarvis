import { NextResponse } from 'next/server'
import { randomBytes } from 'node:crypto'
import { getStore } from '@/lib/bridge/db'
import {
  findEnvironment,
  getOrCreateSession,
  enqueueWork,
  appendSessionEvent,
} from '@/lib/bridge/store'
import { getUserId } from '@/lib/auth-helpers'
import { emitWorkAvailable } from '@/lib/bridge/events'
import { bridgeError } from '@/lib/bridge/errors'

// POST /api/bridge/v1/tasks — the /code UI dispatches a coding task: registers a
// session on the chosen environment (machine) and enqueues the prompt as work
// for the worker to claim. Returns session_id so the UI can tail its events.
// Unauthenticated like the other v1 routes (relies on the 127.0.0.1 bind).
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
    appendSessionEvent(store, sessionId, {
      type: 'user_prompt',
      payload: { prompt: body.prompt },
    })
    const work = enqueueWork(store, body.environment_id, {
      session_id: sessionId,
      data: { type: 'prompt', id: sessionId, prompt: body.prompt },
    })
    workId = work.id
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
  // Past the catch — the rows are committed; a listener throw mustn't turn a
  // successful dispatch into a 500 (matches admin/enqueue).
  emitWorkAvailable(body.environment_id)
  return NextResponse.json({ session_id: sessionId, work_id: workId }, { status: 200 })
}
