import { NextResponse } from 'next/server'
import { getStore } from '@/lib/bridge/db'
import {
  upsertPushSubscription,
  deletePushSubscription,
} from '@/lib/bridge/store'
import { getUserIdOrSharedLocal } from '@/lib/auth-helpers'
import { pushEnabled } from '@/lib/push/web-push'

type IncomingSub = {
  endpoint?: string
  keys?: { p256dh?: string; auth?: string }
}

// POST /api/push/subscribe — register the browser's Web Push subscription for
// the signed-in user (Dispatch phone notifications). Same-origin browser call,
// so the handler resolves the user itself; fails soft (enabled:false) when
// VAPID isn't configured so the client degrades to in-tab notifications.
export async function POST(req: Request): Promise<NextResponse> {
  if (!pushEnabled()) return NextResponse.json({ enabled: false })
  const userId = await getUserIdOrSharedLocal(req.headers)
  if (!userId) {
    return NextResponse.json({ error: 'unauthenticated' }, { status: 401 })
  }
  const body = (await req.json().catch(() => null)) as { subscription?: IncomingSub } | null
  const sub = body?.subscription
  if (!sub?.endpoint || !sub.keys?.p256dh || !sub.keys?.auth) {
    return NextResponse.json({ error: 'invalid subscription' }, { status: 400 })
  }
  upsertPushSubscription(getStore(), {
    endpoint: sub.endpoint,
    user_id: userId,
    p256dh: sub.keys.p256dh,
    auth: sub.keys.auth,
    ua: req.headers.get('user-agent'),
  })
  return NextResponse.json({ enabled: true, ok: true })
}

// DELETE /api/push/subscribe — unsubscribe this endpoint (best-effort; endpoints
// also self-prune on 404/410 at send time).
export async function DELETE(req: Request): Promise<NextResponse> {
  const body = (await req.json().catch(() => null)) as { endpoint?: string } | null
  if (!body?.endpoint) {
    return NextResponse.json({ error: 'endpoint required' }, { status: 400 })
  }
  deletePushSubscription(getStore(), body.endpoint)
  return NextResponse.json({ ok: true })
}
