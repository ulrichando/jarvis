import "server-only";
import { getContainerDiff, mergeContainerPR } from "./containers";
import { githubPrStatus } from "../connectors/github";
import {
  appendUserText,
  archiveSession,
  listAutofixSessions,
  listAutomergeSessions,
  setSessionAutofixSha,
  type Store,
} from "./store";

/**
 * One auto-fix-CI pass (the background loop, run by instrumentation's interval
 * and the /code/autofix/tick endpoint). For each session with auto-fix on and
 * an open PR whose head commit has failing CI — and that we haven't already
 * fixed for that commit — message the session to fix it. Idempotent per commit
 * via `autofix_sha`. Returns how many fix requests were sent.
 */
export async function runAutofixTick(store: Store): Promise<number> {
  let fixed = 0;
  for (const s of listAutofixSessions(store)) {
    const meta = s.container_json
      ? (JSON.parse(s.container_json) as { repo?: string })
      : null;
    if (!meta?.repo) continue;
    // Summary-only diff just to learn the branch (cheap).
    const diff = await getContainerDiff(store, s.session_id, undefined, true);
    if ("error" in diff || !diff.branch || diff.branch === "HEAD" || diff.ahead === 0) {
      continue;
    }
    const st = await githubPrStatus(meta.repo, diff.branch);
    if (!st.ok) continue;
    const { checks, sha } = st.status;
    if (!checks || checks.failed === 0 || !sha || sha === s.autofix_sha) continue;
    // Mark this commit handled BEFORE messaging, so a slow agent turn can't get
    // double-fixed by the next tick.
    setSessionAutofixSha(store, s.session_id, sha);
    appendUserText(
      store,
      s.session_id,
      `The CI checks (${checks.failing.join(", ") || "on this PR"}) are failing. Investigate the failures, fix them, and push the fix to the same branch.`,
    );
    fixed++;
  }
  return fixed;
}

/**
 * One auto-merge pass: for each session with auto-merge on whose PR is open and
 * has all checks passing, squash-merge it. Merging is terminal (PR state flips
 * to merged), so no per-commit dedupe is needed.
 */
export async function runAutomergeTick(store: Store): Promise<number> {
  let merged = 0;
  for (const s of listAutomergeSessions(store)) {
    const meta = s.container_json
      ? (JSON.parse(s.container_json) as { repo?: string })
      : null;
    if (!meta?.repo) continue;
    const diff = await getContainerDiff(store, s.session_id, undefined, true);
    if ("error" in diff || !diff.branch || diff.branch === "HEAD") continue;
    const st = await githubPrStatus(meta.repo, diff.branch);
    if (!st.ok || !st.status.pr || st.status.pr.state !== "open") continue;
    const c = st.status.checks;
    // Only merge once every check is green (some exist, none failing/pending).
    if (!c || c.total === 0 || c.failed > 0 || c.pending > 0) continue;
    const r = await mergeContainerPR(store, s.session_id);
    if (!("error" in r)) {
      // Merged → the work is done; archive the session (claude.ai/code
      // auto-archives on PR merge). The container is reaped by the idle tick.
      archiveSession(store, s.session_id);
      merged++;
    }
  }
  return merged;
}
