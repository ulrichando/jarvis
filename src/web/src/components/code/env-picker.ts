// Which environments the /code composer's picker offers — claude.ai/code
// parity. Shown: real machines + repo-less cloud envs (the persistent "Default"
// env and any named "Add cloud environment…" ones). Hidden: a per-repo
// container env (worker_type 'container' WITH a git_repo_url) — that's an
// ephemeral sandbox auto-created when a repo session launches, a runtime rather
// than a user-selectable environment (claude.ai/code lists environments, not
// per-repo runtimes). It still rides in the page's full `machines` list — this
// only filters the picker view — so deep-link session restore can resolve a
// repo session's env (see session-liveness.ts::shouldAutoOpenSession).

export interface PickableEnv {
  worker_type: string;
  git_repo_url: string | null;
}

export function isPickableEnv(m: PickableEnv): boolean {
  return !(m.worker_type === "container" && !!m.git_repo_url);
}
