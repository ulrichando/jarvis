/**
 * Stub for the optional COMMIT_ATTRIBUTION feature.
 *
 * The original CLI ships a full prepare-commit-msg hook installer here that
 * adds Anthropic attribution trailers when feature('COMMIT_ATTRIBUTION') is
 * on. This vendored copy of the CLI doesn't ship that path — and per
 * project memory, the user explicitly does NOT want Co-Authored-By /
 * "Generated with Claude Code" trailers on commits anyway.
 *
 * Keeping a no-op stub here so the dynamic `import('./postCommitAttribution.js')`
 * call in worktree.ts type-checks cleanly. At runtime it's already wrapped
 * in a try/catch, so behaviour is unchanged.
 */

export async function installPrepareCommitMsgHook(
  _worktreePath: string,
  _worktreeHooksDir: string | undefined,
): Promise<void> {
  // intentional no-op
}
