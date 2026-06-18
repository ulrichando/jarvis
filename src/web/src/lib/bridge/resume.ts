import "server-only";
import { getStore } from "./db";
import { findSession, latestSessionEventAt } from "./store";
import { resumeContainerWorker } from "./containers";

// Auto-resume a container session's worker when the user reopens it. The
// events poll (sessions/{id}/events) calls maybeResumeOnAttach on every tick;
// the cheap gates below keep it to at most one docker check per session per
// cooldown, and resumeContainerWorker is the authority on whether a relaunch
// is actually needed (it no-ops if a worker is already alive).
//
// Why this exists: the worker is a detached process in the container talking
// to this server over SSE. A web-server restart (or crash) drops that
// connection and the worker exits — the container + working tree survive, but
// nothing relaunches the worker, so the session view spins forever. Reopening
// the session is the natural moment to reconnect it.

const lastCheck = new Map<string, number>();
const inFlight = new Set<string>();
const CHECK_COOLDOWN_MS = 20_000;
// Don't fight an in-flight launch or an active turn: during launch/streaming,
// events arrive within seconds, so a recent event means "busy", not "dead".
const QUIESCENT_MS = 20_000;

export function maybeResumeOnAttach(sessionId: string): void {
  void (async () => {
    try {
      const now = Date.now();
      if (inFlight.has(sessionId)) return;
      if (now - (lastCheck.get(sessionId) ?? 0) < CHECK_COOLDOWN_MS) return;
      lastCheck.set(sessionId, now);

      const store = getStore();
      const session = findSession(store, sessionId);
      // Only container sessions resume; archived ones stay stopped.
      if (!session?.container_json || session.archived) return;

      const lastTs = latestSessionEventAt(store, sessionId);
      if (lastTs && now - lastTs < QUIESCENT_MS) return;

      inFlight.add(sessionId);
      try {
        await resumeContainerWorker(store, sessionId);
      } finally {
        inFlight.delete(sessionId);
      }
    } catch {
      /* best-effort: the next poll retries after the cooldown */
    }
  })();
}

/** Test seam: clear the in-memory cooldown/in-flight state between cases. */
export function _resetResumeStateForTests(): void {
  lastCheck.clear();
  inFlight.clear();
}
