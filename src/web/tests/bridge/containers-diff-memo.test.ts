import { describe, expect, test, beforeEach, afterEach, vi } from 'vitest'
import { _resetForTests, getStore } from '@/lib/bridge/db'
import {
  getOrCreateSession,
  createEnvironment,
  setSessionContainer,
  appendSessionEvent,
} from '@/lib/bridge/store'
import {
  getContainerDiff,
  _resetDiffMemoForTests,
  type DockerExec,
} from '@/lib/bridge/containers'

// Hermetic deps (mirrors containers.test.ts).
vi.mock('@/lib/auth-helpers', () => ({
  getUserId: async () => '00000000-0000-0000-0000-000000000001',
}))
vi.mock('@/lib/connectors/github', () => ({
  getGithubToken: async () => 'ghp_test_token',
  githubStatus: async () => ({ connected: true, login: 'tester' }),
}))
vi.mock('@/lib/mcp/store', () => ({ listMcpServers: vi.fn(async () => []) }))

beforeEach(() => {
  _resetForTests()
  _resetDiffMemoForTests()
})

afterEach(() => {
  vi.useRealTimers()
})

function seedContainerSession(id = 'di5f0011223344aa'): string {
  const store = getStore()
  const env = createEnvironment(store, {
    machine_name: 'Cloud container',
    directory: '/workspace',
    git_repo_url: 'https://github.com/owner/demo',
    max_sessions: 4,
    worker_type: 'container',
    user_id: '00000000-0000-0000-0000-000000000001',
  })
  getOrCreateSession(store, id, env.environment_id)
  setSessionContainer(store, id, { container: 'jarvis-code-x', repo: 'owner/demo' })
  return id
}

/** A docker fake whose container reads (those carrying the @@BRANCH@@ probe)
 *  are counted, so tests can assert how many real `git diff` execs happened. */
function countingDocker(stat: string) {
  let containerReads = 0
  const out = [
    '@@BRANCH@@jarvis/x',
    '@@BASE@@origin/main',
    '@@AHEAD@@1',
    '@@STAT@@',
    stat,
    '@@DIFF@@',
    'diff --git a/f b/f',
    '+x',
  ].join('\n')
  const exec: DockerExec = async (args) => {
    if (args[0] === 'exec' && args.some((a) => a.includes('@@BRANCH@@'))) {
      containerReads++
      return { stdout: out, stderr: '' }
    }
    return { stdout: '', stderr: '' }
  }
  return { exec, reads: () => containerReads }
}

describe('summary-diff memo (finding #14)', () => {
  test('a second summary read within the TTL reuses the memo — no extra docker exec', async () => {
    vi.useFakeTimers()
    const id = seedContainerSession()
    const store = getStore()
    const dk = countingDocker(' f | 1 +\n 1 file changed, 1 insertion(+)')

    const first = await getContainerDiff(store, id, dk.exec, true)
    expect('error' in first).toBe(false)
    const readsAfterFirst = dk.reads()
    expect(readsAfterFirst).toBeGreaterThan(0)

    // Advance well under the 5s TTL; a fresh viewer polls the same session.
    vi.setSystemTime(Date.now() + 2_000)
    const second = await getContainerDiff(store, id, dk.exec, true)
    // Byte-identical served result, and NOT one new container read.
    expect(second).toEqual(first)
    expect(dk.reads()).toBe(readsAfterFirst)
  })

  test('past the TTL, the memo expires and a fresh exec fires', async () => {
    vi.useFakeTimers()
    const id = seedContainerSession()
    const store = getStore()
    const dk = countingDocker(' f | 1 +\n 1 file changed, 1 insertion(+)')

    await getContainerDiff(store, id, dk.exec, true)
    const readsAfterFirst = dk.reads()

    // Cross the 5s TTL → the next summary read must hit the container again.
    vi.setSystemTime(Date.now() + 6_000)
    await getContainerDiff(store, id, dk.exec, true)
    expect(dk.reads()).toBeGreaterThan(readsAfterFirst)
  })

  test('a new session event busts the memo before the TTL (badge never stale post-turn)', async () => {
    vi.useFakeTimers()
    const id = seedContainerSession()
    const store = getStore()
    const dk = countingDocker(' f | 1 +\n 1 file changed, 1 insertion(+)')

    await getContainerDiff(store, id, dk.exec, true)
    const readsAfterFirst = dk.reads()

    // A turn lands (tool result / assistant output) inside the TTL window.
    vi.setSystemTime(Date.now() + 1_000)
    appendSessionEvent(store, id, { type: 'assistant', payload: { text: 'edited a file' } })

    // Still within the 5s TTL, but the new event forces a fresh read.
    const after = await getContainerDiff(store, id, dk.exec, true)
    expect('error' in after).toBe(false)
    expect(dk.reads()).toBeGreaterThan(readsAfterFirst)
  })

  test('full (non-summary) reads bypass the memo entirely', async () => {
    vi.useFakeTimers()
    const id = seedContainerSession()
    const store = getStore()
    const dk = countingDocker(' f | 1 +\n 1 file changed, 1 insertion(+)')

    // Two full reads back-to-back both hit the container (never memoized).
    await getContainerDiff(store, id, dk.exec, false)
    const readsAfterFirst = dk.reads()
    await getContainerDiff(store, id, dk.exec, false)
    expect(dk.reads()).toBeGreaterThan(readsAfterFirst)
  })

  test('the memo is per-session — one session\'s memo never serves another', async () => {
    vi.useFakeTimers()
    const a = seedContainerSession('aaaa0011223344aa')
    const b = seedContainerSession('bbbb0011223344bb')
    const store = getStore()
    const dkA = countingDocker(' a | 1 +\n 1 file changed, 1 insertion(+)')
    const dkB = countingDocker(' b | 2 +\n 1 file changed, 2 insertions(+)')

    const ra = await getContainerDiff(store, a, dkA.exec, true)
    const rb = await getContainerDiff(store, b, dkB.exec, true)
    expect('error' in ra).toBe(false)
    expect('error' in rb).toBe(false)
    if ('error' in ra || 'error' in rb) return
    // Distinct stats prove the memo didn't cross sessions.
    expect(ra.stat).toContain('1 insertion(+)')
    expect(rb.stat).toContain('2 insertions(+)')
    expect(dkB.reads()).toBeGreaterThan(0)
  })
})
