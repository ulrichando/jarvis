import { NextResponse } from 'next/server'
import { authorizeSessionToken } from '@/lib/bridge/authz'

// POST /api/bridge/v1/code/sessions/{id}/worker/events/delivery — inbound
// delivery acks ({ worker_epoch, updates: [{event_id, status}] }). The
// self-hosted queue replays by sequence number (from_sequence_num), so the
// acks are accepted but not stored.
export async function POST(
  req: Request,
  ctx: { params: Promise<{ sessionId: string }> },
): Promise<NextResponse> {
  const { sessionId } = await ctx.params
  const denied = authorizeSessionToken(req, sessionId)
  if (denied) return denied
  await req.json().catch(() => null)
  return new NextResponse(null, { status: 204 })
}
