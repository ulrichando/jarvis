// Finding #9 (advisor-plans/007): a FAILED container launch used to reap only
// the workbench container — the jarvis-egress-<id> squid proxy and the
// jarvis-net-<id> private network leaked, and the orphan sweep skipped
// non-jarvis-code- names so they were never reclaimed. These tests pin the
// fix: step()'s failure handler runs the full teardown trio, and the sweep
// maps jarvis-egress-<id> names back to sessions.
import { describe, expect, test, beforeEach, afterEach, vi } from 'vitest'
import { _resetForTests, getStore } from '@/lib/bridge/db'
import {
  getOrCreateSession,
  createEnvironment,
  findSession,
  setEnvironmentConfig,
  setSessionContainer,
} from '@/lib/bridge/store'
import {
  launchContainerSession,
  runOrphanContainerSweep,
  containerNameFor,
  type DockerExec,
} from '@/lib/bridge/containers'

vi.mock('@/lib/auth-helpers', () => ({
  getUserId: async () => '00000000-0000-0000-0000-000000000001',
}))
vi.mock('@/lib/connectors/github', () => ({
  getGithubToken: async () => 'ghp_test_token',
  githubStatus: async () => ({ connected: true, login: 'tester' }),
  openPullRequest: vi.fn(async () => ({ ok: true, url: 'https://github.com/owner/demo/pull/7', number: 7 })),
  mergePullRequest: vi.fn(async () => ({ ok: true })),
  githubPrStatus: vi.fn(async () => ({
    ok: true,
    status: { pr: { number: 7, url: 'https://github.com/owner/demo/pull/7', state: 'open', draft: false }, checks: null, sha: null },
  })),
}))
// Hermetic MCP: no connectors injected (the real ~/.jarvis/mcp.json would
// otherwise leak a Connectors status into these tests).
vi.mock('@/lib/mcp/store', () => ({ listMcpServers: vi.fn(async () => []) }))

beforeEach(() => {
  _resetForTests()
  // Model-proxy health probe defaults to "down" so the direct-Anthropic path
  // runs deterministically regardless of a real proxy on the dev box.
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      throw new Error('no proxy in tests')
    }),
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
})

/** Fake docker that records calls; per-step failures injected via `failOn`. */
function fakeDocker(opts?: { failOn?: (args: string[]) => boolean }) {
  const calls: string[][] = []
  const exec: DockerExec = async (args) => {
    calls.push(args)
    // Include the failing command in the message so assertions can tell WHICH
    // error surfaced (original step vs teardown) — they otherwise all look alike.
    if (opts?.failOn?.(args)) throw new Error(`boom from docker: ${args.join(' ')}`)
    if (args.some((a) => a.includes('test -f'))) return { stdout: 'no\n', stderr: '' }
    return { stdout: '', stderr: '' }
  }
  return { calls, exec }
}

function makeIsolatedSession(sessionId: string): void {
  const store = getStore()
  const env = createEnvironment(store, {
    machine_name: 'Cloud container',
    directory: '/workspace',
    git_repo_url: 'https://github.com/owner/demo',
    max_sessions: 4,
    worker_type: 'container',
    user_id: '00000000-0000-0000-0000-000000000001',
  })
  getOrCreateSession(store, sessionId, env.environment_id)
  // Non-`full` network level → the launch creates jarvis-net-<id> +
  // jarvis-egress-<id> BEFORE the first failable step.
  setEnvironmentConfig(store, env.environment_id, {
    envVars: {},
    setupScript: '',
    networkLevel: 'trusted',
    customAllowlist: [],
  })
}

describe('launch failure teardown (egress proxy + network must not leak)', () => {
  test('a launch failing BEFORE setSessionContainer (workbench run) reaps proxy + network + container', async () => {
    const sessionId = 'dead000011223344'
    makeIsolatedSession(sessionId)
    const store = getStore()
    // Fail the workbench `docker run` itself — the squid proxy run is
    // .catch-wrapped, so target the jarvis-code- run. At this point the
    // network + egress proxy already exist, and container_json was never
    // written (the exact leak window from the finding).
    const { calls, exec } = fakeDocker({
      failOn: (args) => args[0] === 'run' && args.includes(containerNameFor(sessionId)),
    })

    await expect(
      launchContainerSession(store, {
        sessionId,
        repoFullName: 'owner/demo',
        baseUrl: 'http://127.0.0.1:3000',
        proxyHealthy: async () => false,
        exec,
      }),
    ).rejects.toThrow()

    // container_json is NULL → idle-reclaim will never see this session, so
    // the teardown at failure time is the ONLY thing standing between us and
    // a leaked proxy + network.
    expect(findSession(store, sessionId)!.container_json).toBeNull()

    const flat = calls.map((c) => c.join(' '))
    const failIdx = flat.findIndex((c) => c.startsWith('run -d') && c.includes(containerNameFor(sessionId)))
    const after = flat.slice(failIdx + 1)
    expect(after).toContain(`rm -f ${containerNameFor(sessionId)}`)
    expect(after).toContain(`rm -f jarvis-egress-${sessionId}`)
    expect(after).toContain(`network rm jarvis-net-${sessionId}`)
    // Containers removed BEFORE the network — docker refuses to rm a network
    // with containers still attached.
    expect(after.indexOf(`rm -f jarvis-egress-${sessionId}`)).toBeLessThan(
      after.indexOf(`network rm jarvis-net-${sessionId}`),
    )
  })

  test('a launch failing mid-flight (git clone) also runs the full trio', async () => {
    const sessionId = 'dead111122223344'
    makeIsolatedSession(sessionId)
    const store = getStore()
    const { calls, exec } = fakeDocker({
      failOn: (args) => args.join(' ').includes('git clone'),
    })

    await expect(
      launchContainerSession(store, {
        sessionId,
        repoFullName: 'owner/demo',
        baseUrl: 'http://127.0.0.1:3000',
        proxyHealthy: async () => false,
        exec,
      }),
    ).rejects.toThrow()

    const flat = calls.map((c) => c.join(' '))
    const failIdx = flat.findIndex((c) => c.includes('git clone'))
    const after = flat.slice(failIdx + 1)
    expect(after).toContain(`rm -f ${containerNameFor(sessionId)}`)
    expect(after).toContain(`rm -f jarvis-egress-${sessionId}`)
    expect(after).toContain(`network rm jarvis-net-${sessionId}`)
  })

  test('teardown is best-effort: a failing proxy/network rm never masks the step error', async () => {
    const sessionId = 'dead222233334444'
    makeIsolatedSession(sessionId)
    const store = getStore()
    // Everything in the teardown trio fails too (already-gone containers) —
    // the ORIGINAL step error must still surface, not a teardown error.
    const { exec } = fakeDocker({
      failOn: (args) =>
        args.join(' ').includes('git clone') ||
        (args[0] === 'rm' && args[1] === '-f') ||
        (args[0] === 'network' && args[1] === 'rm'),
    })

    await expect(
      launchContainerSession(store, {
        sessionId,
        repoFullName: 'owner/demo',
        baseUrl: 'http://127.0.0.1:3000',
        proxyHealthy: async () => false,
        exec,
      }),
    ).rejects.toThrow(/git clone/)
  })
})

describe('runOrphanContainerSweep — egress name mapping', () => {
  // Same labeled-docker fake shape as containers.test.ts: `ps` lists names;
  // `inspect` answers StartedAt (default 1h ago = old); rm/network rm succeed.
  function sweepDocker(names: string[], startedAt: Record<string, string> = {}) {
    const calls: string[][] = []
    const old = new Date(Date.now() - 60 * 60 * 1000).toISOString()
    const exec: DockerExec = async (args) => {
      calls.push(args)
      if (args[0] === 'ps') return { stdout: names.join('\n') + '\n', stderr: '' }
      if (args[0] === 'inspect') {
        const name = args[args.length - 1]
        return { stdout: (startedAt[name] ?? old) + '\n', stderr: '' }
      }
      return { stdout: '', stderr: '' }
    }
    return { calls, exec }
  }
  const removed = (calls: string[][]) =>
    calls.filter((c) => c[0] === 'rm' && c[1] === '-f').map((c) => c[2])

  test('reaps an orphaned egress proxy whose workbench never started (no session row)', async () => {
    const sessionId = 'ec0ffee011223344'
    const { calls, exec } = sweepDocker([`jarvis-egress-${sessionId}`])
    const reaped = await runOrphanContainerSweep(getStore(), exec)
    expect(reaped).toBe(1)
    // stopContainerSession reaps the whole trio for the mapped session.
    const rm = removed(calls)
    expect(rm).toContain(`jarvis-egress-${sessionId}`)
    expect(rm).toContain(containerNameFor(sessionId))
    expect(calls).toContainEqual(['network', 'rm', `jarvis-net-${sessionId}`])
  })

  test('a code+egress pair for the same dead session is reaped ONCE (deduped by session)', async () => {
    const sessionId = 'ec0ffee111223344'
    const { calls, exec } = sweepDocker([
      containerNameFor(sessionId),
      `jarvis-egress-${sessionId}`,
    ])
    const reaped = await runOrphanContainerSweep(getStore(), exec)
    expect(reaped).toBe(1)
    // Exactly one teardown trio — not one per name.
    expect(removed(calls).filter((n) => n === containerNameFor(sessionId))).toHaveLength(1)
    expect(removed(calls).filter((n) => n === `jarvis-egress-${sessionId}`)).toHaveLength(1)
  })

  test('spares the egress proxy of a tracked, live session (idle reclaim owns it)', async () => {
    const store = getStore()
    const env = createEnvironment(store, {
      machine_name: 'Cloud container',
      directory: '/workspace',
      git_repo_url: 'https://github.com/owner/demo',
      max_sessions: 4,
      worker_type: 'container',
      user_id: '00000000-0000-0000-0000-000000000001',
    })
    getOrCreateSession(store, 'a11ve00011223344', env.environment_id)
    setSessionContainer(store, 'a11ve00011223344', {
      container: containerNameFor('a11ve00011223344'),
      repo: 'owner/demo',
    })
    const { calls, exec } = sweepDocker([
      containerNameFor('a11ve00011223344'),
      'jarvis-egress-a11ve00011223344',
    ])
    expect(await runOrphanContainerSweep(store, exec)).toBe(0)
    expect(removed(calls)).toHaveLength(0)
  })

  test('spares a freshly-started egress orphan (mid-launch race guard)', async () => {
    const sessionId = 'f4e5h00011223344'
    const name = `jarvis-egress-${sessionId}`
    const { calls, exec } = sweepDocker([name], { [name]: new Date().toISOString() })
    expect(await runOrphanContainerSweep(getStore(), exec)).toBe(0)
    expect(removed(calls)).toHaveLength(0)
  })

  test('still ignores containers with neither prefix', async () => {
    const { calls, exec } = sweepDocker(['some-other-container', 'jarvis-ws-abc'])
    expect(await runOrphanContainerSweep(getStore(), exec)).toBe(0)
    expect(removed(calls)).toHaveLength(0)
  })
})
