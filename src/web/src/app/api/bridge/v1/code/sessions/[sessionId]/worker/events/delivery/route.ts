import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import { validateSessionToken } from '@/lib/bridge/store'
import { extractBearer } from '@/lib/bridge/auth'
import { bridgeError } from '@/lib/bridge/errors'

// POST /api/bridge/v1/code/sessions/{id}/worker/events/delivery — inbound
// delivery acks ({ worker_epoch, updates: [{event_id, status}] }). The
// self-hosted queue replays by sequence number (from_sequence_num), so the
// acks are accepted but not stored.
export async function POST(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params
  const token = extractBearer(req.headers.get('authorization'))
  if (!token) return bridgeError(401, 'unauthorized', 'Missing bearer')
  const store = getStore()
  if (!validateSessionToken(store, sessionId, token)) {
    return bridgeError(401, 'unauthorized', 'Invalid session token')
  }
  await req.json().catch(() => null)
  return new NextResponse(null, { status: 204 })
}
