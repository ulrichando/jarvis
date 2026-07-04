import { describe, expect, test } from 'vitest'
import { isPickableEnv } from '@/components/code/env-picker'

// The /code picker shows selectable environments (the "Default" cloud env, any
// named "Add cloud environment…" ones, and real machines) but NOT the per-repo
// container sandboxes auto-created when a repo session launches — those are
// runtimes, hidden to match claude.ai/code (which lists environments, not
// per-repo runtimes). Regression guard for the "Cloud container appeared in my
// picker" report.
describe('isPickableEnv', () => {
  test('repo-less cloud env (Default / named) is pickable', () => {
    expect(isPickableEnv({ worker_type: 'container', git_repo_url: null })).toBe(true)
  })

  test('per-repo container sandbox is hidden', () => {
    expect(
      isPickableEnv({ worker_type: 'container', git_repo_url: 'https://github.com/o/r' }),
    ).toBe(false)
  })

  test('a real machine stays pickable even with a repo dir', () => {
    expect(
      isPickableEnv({ worker_type: 'claude_code_repl', git_repo_url: 'https://github.com/o/r' }),
    ).toBe(true)
  })
})
