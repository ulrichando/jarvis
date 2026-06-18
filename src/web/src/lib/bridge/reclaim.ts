import "server-only";
import { runOrphanContainerSweep, stopContainerSession } from "./containers";
import { clearSessionContainer, listIdleContainerSessions, type Store } from "./store";

/**
 * Reap containers for sessions idle past JARVIS_CODE_IDLE_RECLAIM_HOURS (default
 * 12; 0 disables). Frees docker resources from abandoned /code sessions (which
 * otherwise pile up — claude.ai/code reclaims idle environments the same way).
 * The transcript stays; only the (long-idle, worker-done) container is removed.
 *
 * Two passes: (1) DB-driven — sessions we still track that have gone idle; and
 * (2) an orphan sweep over our labeled containers whose session the DB no
 * longer tracks (deleted/archived/cleared records whose `docker rm` never
 * landed). Pass 1 alone left orphans running indefinitely (observed: 5
 * containers up 46h+ that pass 1 could never see). Returns how many were
 * reaped.
 */
export async function runReclaimTick(store: Store): Promise<number> {
  const hours = Number(process.env.JARVIS_CODE_IDLE_RECLAIM_HOURS ?? "12");
  if (!Number.isFinite(hours) || hours <= 0) return 0;
  const before = Date.now() - hours * 3600_000;
  let reaped = 0;
  for (const s of listIdleContainerSessions(store, before)) {
    await stopContainerSession(store, s.session_id);
    clearSessionContainer(store, s.session_id);
    reaped++;
  }
  reaped += await runOrphanContainerSweep(store);
  return reaped;
}
