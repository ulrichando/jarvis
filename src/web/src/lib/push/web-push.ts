import webpush from 'web-push'
import {
  deletePushSubscription,
  listPushSubscriptions,
  type Store,
} from '@/lib/bridge/store'

// Node-only (web-push + better-sqlite3). NEVER import this from proxy.ts or an
// Edge-designated route.

export type Vapid = { publicKey: string; privateKey: string; subject: string }

/** VAPID config from env, or null when unset — every caller must fail SOFT
 *  (feature disabled) rather than 500 when push isn't configured. Generate once
 *  with `npx web-push generate-vapid-keys`. */
export function getVapid(): Vapid | null {
  const publicKey = process.env.JARVIS_VAPID_PUBLIC_KEY
  const privateKey = process.env.JARVIS_VAPID_PRIVATE_KEY
  if (!publicKey || !privateKey) return null
  // A mailto:/https: subject is required by the spec; default to a placeholder
  // so a missing subject doesn't disable push (the key pair is what matters).
  const subject = process.env.JARVIS_VAPID_SUBJECT || 'mailto:admin@localhost'
  return { publicKey, privateKey, subject }
}

export function pushEnabled(): boolean {
  return getVapid() !== null
}

/** Send a notification to every one of a user's subscriptions. Prunes any
 *  endpoint the push service reports as gone (404/410). No-op (returns 0) when
 *  VAPID is unconfigured. Returns how many pushes were delivered. */
export async function sendPushToUser(
  store: Store,
  userId: string,
  payload: { title: string; body: string; url?: string; tag?: string },
): Promise<number> {
  const vapid = getVapid()
  if (!vapid) return 0
  webpush.setVapidDetails(vapid.subject, vapid.publicKey, vapid.privateKey)
  const subs = listPushSubscriptions(store, userId)
  const data = JSON.stringify(payload)
  let sent = 0
  await Promise.all(
    subs.map(async (s) => {
      try {
        await webpush.sendNotification(
          { endpoint: s.endpoint, keys: { p256dh: s.p256dh, auth: s.auth } },
          data,
        )
        sent++
      } catch (err) {
        const code = (err as { statusCode?: number }).statusCode
        if (code === 404 || code === 410) deletePushSubscription(store, s.endpoint)
        // Other errors (network, 5xx) are transient — leave the row for retry.
      }
    }),
  )
  return sent
}
