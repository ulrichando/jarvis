export function clearContextCollapseStore(): void {}

export function readContextCollapseStore(): [] {
  return []
}

export function writeContextCollapseStore(_entries: readonly unknown[]): void {}

// Context-collapse is stubbed in jarvis (the store fns above are no-ops), but
// the session-restore / resume path calls restoreFromEntries(commits, snapshot)
// unconditionally (sessionRestore.ts, ResumeConversation.tsx). Without it the
// require yields undefined and EVERY resume — /resume AND /teleport's in-place
// resume — throws "restoreFromEntries is not a function". No-op stub matching
// the rest of this module: there's no collapse store to rebuild.
export function restoreFromEntries(_commits: readonly unknown[], _snapshot?: unknown): void {}
export {}
