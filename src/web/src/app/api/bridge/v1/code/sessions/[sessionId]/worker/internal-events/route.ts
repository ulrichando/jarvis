import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import {
  appendInternalEvents,
  listInternalEvents,
} from '@/lib/bridge/store'
import { authorizeSessionToken } from '@/lib/bridge/authz'
import { bridgeError } from '@/lib/bridge/errors'

// CCR v2 worker internal events — transcript/compaction state the CLI needs
// back on session resume. NOT shown in the /code UI (separate table).
// POST body: { worker_epoch, events: [...] }. GET (?subagents=true) returns
// { data: [...] } — single page, no next_cursor (paginatedGet stops there).

export async function POST(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params
  const denied = authorizeSessionToken(req, sessionId)
  if (denied) return denied
  const body = (await req.json().catch(() => null)) as {
    events?: unknown[]
  } | null
  if (!body || !Array.isArray(body.events)) {
    return bridgeError(400, 'invalid_request', 'events array required')
  }
  try {
    const store = getStore()
    const isSubagent = (e: unknown): boolean =>
      !!e &&
      typeof e === 'object' &&
      'agent_id' in e &&
      typeof (e as { agent_id?: unknown }).agent_id === 'string' &&
      (e as { agent_id: string }).agent_id !== ''
    appendInternalEvents(
      store,
      sessionId,
      body.events.filter((e) => !isSubagent(e)),
      false,
    )
    appendInternalEvents(store, sessionId, body.events.filter(isSubagent), true)
    return NextResponse.json({})
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}

export async function GET(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params
  const denied = authorizeSessionToken(req, sessionId)
  if (denied) return denied
  try {
    const subagents =
      new URL(req.url).searchParams.get('subagents') === 'true'
    const data = listInternalEvents(getStore(), sessionId, subagents)
    return NextResponse.json({ data })
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    return bridgeError(500, 'internal_error', `DB error: ${msg}`)
  }
}
