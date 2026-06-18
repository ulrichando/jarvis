import "server-only";
import {
  listRoutines,
  updateRoutine,
  type GithubFilters,
  type RoutineTrigger,
  type Store,
} from "./store";
import { runRoutine } from "./routines-run";
import { cronIsDue } from "../cron";

/** True if a github-event payload satisfies the routine's filters (every set
 *  field must match). Non-PR events match when only PR-targeting filters exist. */
function matchesGithubFilters(f: GithubFilters | undefined, payload: Record<string, unknown>): boolean {
  if (!f) return true;
  const pr = payload.pull_request as
    | { user?: { login?: string }; title?: string; base?: { ref?: string }; head?: { ref?: string }; labels?: { name?: string }[]; draft?: boolean; merged?: boolean }
    | undefined;
  if (!pr) return true;
  if (f.author && pr.user?.login !== f.author) return false;
  if (f.titleContains && !String(pr.title ?? "").toLowerCase().includes(f.titleContains.toLowerCase())) return false;
  if (f.baseBranch && pr.base?.ref !== f.baseBranch) return false;
  if (f.headBranch && pr.head?.ref !== f.headBranch) return false;
  if (f.labels?.length) {
    const have = (pr.labels ?? []).map((l) => l.name);
    if (!f.labels.every((l) => have.includes(l))) return false;
  }
  if (typeof f.isDraft === "boolean" && Boolean(pr.draft) !== f.isDraft) return false;
  if (typeof f.isMerged === "boolean" && Boolean(pr.merged) !== f.isMerged) return false;
  return true;
}

/**
 * One routines pass (the background scheduler, run by instrumentation's
 * interval and POST /code/autofix/tick's sibling). Fires schedule-trigger
 * routines that are due: recurring crons via cronIsDue, and one-time (`at`)
 * routines once, then pauses them. `runRoutine` stamps last_run_at. Returns
 * how many routines fired.
 */
export async function runRoutinesTick(store: Store, origin: string): Promise<number> {
  let fired = 0;
  const now = Date.now();
  for (const r of listRoutines(store)) {
    if (r.paused) continue;
    let trig: RoutineTrigger;
    try {
      trig = JSON.parse(r.trigger_json) as RoutineTrigger;
    } catch {
      continue;
    }
    if (trig.type !== "schedule") continue; // api/github fire via their own paths

    if (typeof trig.at === "number") {
      // One-time: fire once at/after `at`, then pause so it never repeats.
      if (!r.last_run_at && now >= trig.at) {
        const res = await runRoutine(store, r, origin);
        if (!("error" in res)) {
          updateRoutine(store, r.routine_id, { paused: true });
          fired++;
        }
      }
      continue;
    }

    if (cronIsDue(trig.cron, r.last_run_at, now)) {
      const res = await runRoutine(store, r, origin);
      if (!("error" in res)) fired++;
    }
  }
  return fired;
}

/**
 * Fire github-trigger routines whose event list includes `event` (called by the
 * webhook receiver). Returns how many ran.
 */
export async function runGithubRoutines(
  store: Store,
  origin: string,
  event: string,
  payload: Record<string, unknown> = {},
): Promise<number> {
  let fired = 0;
  for (const r of listRoutines(store)) {
    if (r.paused) continue;
    let trig: RoutineTrigger;
    try {
      trig = JSON.parse(r.trigger_json) as RoutineTrigger;
    } catch {
      continue;
    }
    if (trig.type !== "github" || !trig.events.includes(event)) continue;
    if (!matchesGithubFilters(trig.filters, payload)) continue;
    const res = await runRoutine(store, r, origin);
    if (!("error" in res)) fired++;
  }
  return fired;
}
