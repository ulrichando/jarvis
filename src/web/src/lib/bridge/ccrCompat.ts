import { type SessionRow } from './store'

// Shared helpers for the CCR-compat route group (Phase B). Maps JARVIS bridge
// state to the shapes the intact teleport / ultraplan CLI client expects.
// Spec: docs/superpowers/specs/2026-06-27-jarvis-web-ccr-backend-design.md

export type CcrSessionStatus =
  | 'running'
  | 'idle'
  | 'requires_action'
  | 'archived'

/**
 * Map a JARVIS session row's worker state to the CCR `session_status` the
 * client polls for (`fetchSession`). Defaults to 'running' so the ultraplan
 * poller keeps polling until the ExitPlanMode tool_use lands in the stream
 * (the authoritative signal is the event stream, not this status).
 */
export function ccrSessionStatus(session: SessionRow | null): CcrSessionStatus {
  if (!session) return 'running'
  if (session.archived) return 'archived'
  try {
    const ws = session.worker_state_json
      ? (JSON.parse(session.worker_state_json) as { worker_status?: string })
      : null
    const s = ws?.worker_status
    if (s === 'requires_action') return 'requires_action'
    if (s === 'idle') return 'idle'
    if (s === 'running') return 'running'
  } catch {
    /* malformed worker state — fall through */
  }
  return 'running'
}
