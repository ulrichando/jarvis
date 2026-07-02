// Liveness rule for auto-opening a /code session from the URL on page load.
//
// Why this exists: the /code page keeps the open session in the URL
// (/code/session_<id>, written via history.replaceState) so it's linkable and
// survives refresh. But a `/remote-control` (local "claude_code_repl") session
// only exists while its CLI bridge is alive. When that bridge exits — cleanly
// or not — the session row lingers (archived=0) but its environment stops
// heartbeating, so `environments.last_seen_at` goes stale (env "offline").
//
// A sticky browser tab still pointing at that dead session would silently
// reopen the OLD conversation every time the user lands on /code — the
// "remote control opens with an old conversation in there" bug. So auto-open is
// gated on the session's worker actually being live.
//
// Cloud "container" sessions are different: their transcript is durable and the
// sandbox resumes on demand (claude.ai/code parity), so they stay openable even
// when the sandbox is currently asleep (offline). Only local/REPL sessions are
// meaningless once their bridge is gone.
//
// Manual selection (clicking a session in the sidebar) is intentionally NOT
// gated by this — you can always open a past session to read it. This rule only
// governs the automatic restore-from-URL on load.

export interface LivenessSession {
  session_id: string;
  environment_id?: string | null;
}

export interface LivenessMachine {
  environment_id: string;
  worker_type: string;
  online: boolean;
}

/**
 * Whether /code should auto-open `session` from the URL on load.
 *
 * @param session the session referenced by the URL, or undefined if it's not in
 *   the loaded session list (deleted / beyond the list cap) — never auto-opened.
 * @param machine the session's environment from the machine list, or undefined
 *   if that env is gone/reaped — never auto-opened (can't be live).
 */
export function shouldAutoOpenSession(
  session: LivenessSession | undefined,
  machine: LivenessMachine | undefined,
): boolean {
  if (!session || !machine) return false;
  // Cloud sessions persist + resume → always restorable.
  if (machine.worker_type === "container") return true;
  // Local / `/remote-control` sessions: only while the bridge is live.
  return machine.online;
}
