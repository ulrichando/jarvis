import { NextResponse } from 'next/server'
import { getVapid } from '@/lib/push/web-push'

// GET /api/push/vapid-public-key — the browser needs the VAPID public key to
// call pushManager.subscribe(). Returns { enabled:false } (not an error) when
// push isn't configured, so the client fails soft to in-tab notifications.
export function GET(): NextResponse {
  const vapid = getVapid()
  if (!vapid) return NextResponse.json({ enabled: false, key: null })
  return NextResponse.json({ enabled: true, key: vapid.publicKey })
}
