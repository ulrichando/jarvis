import {
  findSession,
  mergeWorkerState,
  sessionOwnerUserId,
  type Store,
} from '@/lib/bridge/store'
import { sendPushToUser } from './web-push'
import { acquireKeepAwake, releaseKeepAwake } from '@/lib/dispatch/keep-awake'

// Web Push firing for Dispatch sessions. Called from the EXISTING worker route
// handlers (PUT /worker on a status transition; POST /worker/events on a result
// / ultraplan_permission event) — no worker/CLI change. Fire-and-forget from
// the caller (`void fire...`); never block the worker's request.
//
// Dedup: the two observation points (status PUT + events POST) can each see the
// same "done"/"needs a go-ahead", so we stamp a `last_push_status` marker into
// the EXISTING worker_state_json blob. We only push when the marker differs, and
// a `running` transition re-arms it so a re-run notifies again.

// The two notifying states, keyed the same across both fire paths.
type Notify = 'idle' | 'requires_action' // done | needs a go-ahead

async function fire(store: Store, sessionId: string, key: Notify): Promise<void> {
  const session = findSession(store, sessionId)
  if (!session || session.dispatch !== 1) return
  // Task done → drop the keep-awake sleep inhibitor (no-op when not held,
  // e.g. keep_awake was off). Before the push dedup/owner checks on purpose:
  // the lock must release even when the notification doesn't send.
  if (key === 'idle') releaseKeepAwake(sessionId)
  let state: Record<string, unknown> = {}
  try {
    state = session.worker_state_json
      ? (JSON.parse(session.worker_state_json) as Record<string, unknown>)
      : {}
  } catch {
    state = {}
  }
  if (state.last_push_status === key) return // already pushed for this state
  const userId = sessionOwnerUserId(store, sessionId)
  if (!userId) return // orphan/ownerless session → no one to notify
  const name = (session.title || 'Your task').slice(0, 80)
  const msg =
    key === 'requires_action'
      ? { title: 'Jarvis needs a go-ahead', body: `${name} is waiting for your approval` }
      : { title: 'Task finished', body: `${name} is done` }
  // Stamp the marker BEFORE sending so a racing observer on the other path
  // short-circuits (best-effort — a true concurrent read could still double).
  mergeWorkerState(store, sessionId, { last_push_status: key })
  await sendPushToUser(store, userId, {
    ...msg,
    url: `/code/session_${sessionId}`,
    tag: `dispatch-${sessionId}`,
  })
}

/** Fire from PUT /worker when the worker reports a new status. `running`
 *  re-arms the dedup so the next done/needs-input notifies again. */
export function firePushForStatusTransition(
  store: Store,
  sessionId: string,
  nextStatus: string,
): void {
  if (nextStatus === 'running') {
    // Re-arm: a fresh turn started, so a later idle/requires_action pushes again.
    try {
      const s = findSession(store, sessionId)
      if (s?.dispatch === 1) {
        mergeWorkerState(store, sessionId, { last_push_status: 'running' })
        // Keep-awake must cover EVERY active turn, not just the first: each
        // 'idle' releases the lock, so re-acquire when the session goes
        // running again (follow-up prompts). The Map dedup inside
        // acquireKeepAwake makes a redundant acquire a safe no-op.
        if (s.keep_awake) acquireKeepAwake(sessionId)
      }
    } catch {
      /* best-effort */
    }
    return
  }
  if (nextStatus === 'requires_action') void fire(store, sessionId, 'requires_action')
  else if (nextStatus === 'idle') void fire(store, sessionId, 'idle')
}

/** Fire from POST /worker/events: a `result` event (turn done) or an
 *  `ultraplan_permission` event (waiting on a human decision). */
export function firePushForResult(
  store: Store,
  sessionId: string,
  kind: 'done' | 'needs_input',
): void {
  void fire(store, sessionId, kind === 'needs_input' ? 'requires_action' : 'idle')
}
