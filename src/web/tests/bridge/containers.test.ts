import { describe, expect, test, beforeEach, afterEach, vi } from 'vitest'
import { _resetForTests, getStore } from '@/lib/bridge/db'
import {
  getOrCreateSession,
  createEnvironment,
  findSession,
  listSessionEvents,
  listInboundSince,
  appendInbound,
  appendSessionEvent,
  setEnvironmentConfig,
  getWorkerSpec,
  getInboundFloorSeq,
  setSessionContainer,
  archiveSession,
} from '@/lib/bridge/store'
import {
  launchContainerSession,
  resumeContainerWorker,
  stopContainerSession,
  containerNameFor,
  validRepoFullName,
  getContainerDiff,
  createContainerPR,
  mergeContainerPR,
  runOrphanContainerSweep,
  type DockerExec,
} from '@/lib/bridge/containers'

vi.mock('@/lib/auth-helpers', () => ({
  getUserId: async () => '00000000-0000-0000-0000-000000000001',
}))
vi.mock('@/lib/connectors/github', () => ({
  getGithubToken: async () => 'ghp_test_token',
  githubStatus: async () => ({ connected: true, login: 'tester' }),
}))
// Hermetic MCP: no connectors injected by default (the real ~/.jarvis/mcp.json
// would otherwise leak a Connectors status into these tests). The injection
// test overrides this with mockResolvedValueOnce.
vi.mock('@/lib/mcp/store', () => ({ listMcpServers: vi.fn(async () => []) }))

beforeEach(() => {
  _resetForTests()
  // Default the model-proxy (:4000) health probe to "down" so tests exercise
  // the deterministic direct-Anthropic path regardless of whether a real proxy
  // happens to be running on the dev box. Proxy-path tests inject their own
  // `proxyHealthy` (which bypasses fetch entirely).
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      throw new Error('no proxy in tests')
    }),
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
  delete process.env.JARVIS_CODE_SETUP_CACHE
})

/** Fake docker that records calls; per-step failures injected via `failOn`. */
function fakeDocker(opts?: { failOn?: (args: string[]) => boolean; setupScript?: boolean }) {
  const calls: string[][] = []
  const exec: DockerExec = async (args) => {
    calls.push(args)
    if (opts?.failOn?.(args)) throw new Error('boom from docker')
    // The setup-script probe answers via stdout.
    if (args.some((a) => a.includes('test -f'))) {
      return { stdout: opts?.setupScript ? 'yes\n' : 'no\n', stderr: '' }
    }
    return { stdout: '', stderr: '' }
  }
  return { calls, exec }
}

function makeSession(): string {
  const store = getStore()
  const env = createEnvironment(store, {
    machine_name: 'Cloud container',
    directory: '/workspace',
    git_repo_url: 'https://github.com/owner/demo',
    max_sessions: 4,
    worker_type: 'container',
    user_id: '00000000-0000-0000-0000-000000000001',
  })
  getOrCreateSession(store, 'c0ffee0011223344', env.environment_id)
  return 'c0ffee0011223344'
}

describe('launchContainerSession', () => {
  test('runs the init sequence in order and emits the four status steps', async () => {
    const sessionId = makeSession()
    const store = getStore()
    const { calls, exec } = fakeDocker()

    await launchContainerSession(store, {
      sessionId,
      repoFullName: 'owner/demo',
      baseUrl: 'http://127.0.0.1:3000',
      exec,
    })

    // Step order on the docker side: rm (idempotent) → run → clone →
    // remote scrub → setup probe → config write → detached CLI exec.
    const flat = calls.map((c) => c.join(' '))
    const runIdx = flat.findIndex((c) => c.startsWith('run -d'))
    const cloneIdx = flat.findIndex((c) => c.includes('git clone'))
    const scrubIdx = flat.findIndex((c) => c.includes('remote set-url'))
    const cliIdx = flat.findIndex((c) => c.includes('cli.tsx'))
    expect(runIdx).toBeGreaterThanOrEqual(0)
    expect(cloneIdx).toBeGreaterThan(runIdx)
    expect(scrubIdx).toBeGreaterThan(cloneIdx)
    expect(cliIdx).toBeGreaterThan(scrubIdx)

    // The clone uses the connector token; the scrub removes it.
    expect(flat[cloneIdx]).toContain('x-access-token:ghp_test_token@github.com/owner/demo')
    expect(flat[scrubIdx]).toContain('https://github.com/owner/demo.git')

    // The CLI exec carries the worker handshake env (epoch from the web —
    // the spawner role — and the session ingress token), CCR v2 mode, and
    // points --sdk-url at this app.
    const cli = flat[cliIdx]
    expect(cli).toContain('CLAUDE_CODE_USE_CCR_V2=1')
    expect(cli).toContain('CLAUDE_CODE_WORKER_EPOCH=1')
    expect(cli).toContain(
      `--sdk-url 'http://127.0.0.1:3000/api/bridge/v1/code/sessions/${sessionId}'`,
    )
    const session = findSession(store, sessionId)
    expect(cli).toContain(`CLAUDE_CODE_SESSION_ACCESS_TOKEN=${session!.session_token}`)
    expect(session!.worker_epoch).toBe(1)
    expect(JSON.parse(session!.container_json!)).toEqual({
      container: containerNameFor(sessionId),
      repo: 'owner/demo',
    })

    // Status events mirror the claude.ai init block, setup skipped.
    const statuses = listSessionEvents(store, sessionId, 0)
      .map((e) => (JSON.parse(e.payload_json) as { status?: string }).status)
      .filter(Boolean)
    expect(statuses).toEqual([
      '✓ Set up a cloud container',
      '✓ Cloned repository',
      '◌ Run setup script — skipped (no .jarvis/setup.sh in the repo)',
      '✓ Started Jarvis Code',
    ])
  })

  test('configures a push-capable git identity + GH_TOKEN so the agent commits/pushes/PRs on its own', async () => {
    const sessionId = makeSession()
    const store = getStore()
    const { calls, exec } = fakeDocker()

    await launchContainerSession(store, {
      sessionId,
      repoFullName: 'owner/demo',
      baseUrl: 'http://127.0.0.1:3000',
      exec,
    })
    const flat = calls.map((c) => c.join(' '))

    // Committer identity is derived from the connected GitHub login — the
    // agent never has to ask for a name/email (the failure this fixes).
    const gitcfg = flat.find((c) => c.includes('git config --global user.name'))
    expect(gitcfg).toBeTruthy()
    expect(gitcfg).toContain("user.name 'tester'")
    expect(gitcfg).toContain("user.email 'tester@users.noreply.github.com'")
    // A store-backed credential supplies the push token to ~/.git-credentials,
    // so `git push` works without prompting; the remote URL stays clean.
    expect(gitcfg).toContain('credential.helper store')
    expect(gitcfg).toContain('x-access-token:ghp_test_token@github.com')
    expect(gitcfg).toContain('.git-credentials')

    // The CLI child gets GH_TOKEN (so `gh pr create` is pre-authenticated) and
    // an appended prompt instructing it to commit/push/PR proactively.
    const cli = flat.find((c) => c.includes('cli.tsx'))!
    expect(cli).toContain('GH_TOKEN=ghp_test_token')
    expect(cli).toContain('git push')
    expect(cli).toContain('gh pr create')
    expect(cli).toContain('never reply that you were not asked')
  })

  test('with the proxy DOWN, boots the child on the picked Anthropic model via --model', async () => {
    const sessionId = makeSession()
    const store = getStore()
    const { calls, exec } = fakeDocker()

    await launchContainerSession(store, {
      sessionId,
      repoFullName: 'owner/demo',
      baseUrl: 'http://127.0.0.1:3000',
      model: 'claude-opus-4-8',
      proxyHealthy: async () => false,
      exec,
    })
    const cli = calls.map((c) => c.join(' ')).find((c) => c.includes('cli.tsx'))!
    expect(cli).toContain("--model 'claude-opus-4-8'")
  })

  test('with the proxy DOWN, a non-Anthropic pick warns and falls back to Claude (no --model)', async () => {
    const sessionId = makeSession()
    const store = getStore()
    const { calls, exec } = fakeDocker()

    await launchContainerSession(store, {
      sessionId,
      repoFullName: 'owner/demo',
      baseUrl: 'http://127.0.0.1:3000',
      model: 'deepseek-v4-flash',
      proxyHealthy: async () => false,
      exec,
    })
    const cli = calls.map((c) => c.join(' ')).find((c) => c.includes('cli.tsx'))!
    expect(cli).not.toContain('--model')
    const statuses = listSessionEvents(store, sessionId, 0)
      .map((e) => (JSON.parse(e.payload_json) as { status?: string }).status)
      .filter(Boolean)
    expect(statuses.some((s) => s!.includes('needs the local model proxy'))).toBe(true)
  })

  test('with the proxy UP, routes the picked model through it (no --model, JARVIS_* env set)', async () => {
    const sessionId = makeSession()
    const store = getStore()
    const { calls, exec } = fakeDocker()

    await launchContainerSession(store, {
      sessionId,
      repoFullName: 'owner/demo',
      baseUrl: 'http://127.0.0.1:3000',
      model: 'deepseek-v4-flash',
      proxyHealthy: async () => true,
      exec,
    })
    const cli = calls.map((c) => c.join(' ')).find((c) => c.includes('cli.tsx'))!
    // Routed via the local LiteLLM proxy like bin/jarvis — not Anthropic-direct.
    expect(cli).toContain('ANTHROPIC_BASE_URL=http://127.0.0.1:4000')
    expect(cli).toContain('JARVIS_PROVIDER=deepseek')
    expect(cli).toContain('JARVIS_MODEL=deepseek-v4-flash')
    expect(cli).not.toContain('--model')
  })

  test('with the proxy UP and no model picked, defaults to the CLI default (deepseek-v4-pro)', async () => {
    const sessionId = makeSession()
    const store = getStore()
    const { calls, exec } = fakeDocker()

    await launchContainerSession(store, {
      sessionId,
      repoFullName: 'owner/demo',
      baseUrl: 'http://127.0.0.1:3000',
      proxyHealthy: async () => true,
      exec,
    })
    const cli = calls.map((c) => c.join(' ')).find((c) => c.includes('cli.tsx'))!
    expect(cli).toContain('JARVIS_PROVIDER=deepseek')
    expect(cli).toContain('JARVIS_MODEL=deepseek-v4-pro')
  })

  test('runs the setup script when the repo has .jarvis/setup.sh', async () => {
    const sessionId = makeSession()
    const store = getStore()
    const { calls, exec } = fakeDocker({ setupScript: true })

    await launchContainerSession(store, {
      sessionId,
      repoFullName: 'owner/demo',
      baseUrl: 'http://127.0.0.1:3000',
      exec,
    })

    const flat = calls.map((c) => c.join(' '))
    expect(flat.some((c) => c.includes('bash .jarvis/setup.sh'))).toBe(true)
    const statuses = listSessionEvents(store, sessionId, 0)
      .map((e) => (JSON.parse(e.payload_json) as { status?: string }).status)
      .filter(Boolean)
    expect(statuses).toContain('✓ Run setup script')
  })

  test('a failed step emits ✗, reaps the container, and rethrows', async () => {
    const sessionId = makeSession()
    const store = getStore()
    const { calls, exec } = fakeDocker({
      failOn: (args) => args.join(' ').includes('git clone'),
    })

    await expect(
      launchContainerSession(store, {
        sessionId,
        repoFullName: 'owner/demo',
        baseUrl: 'http://127.0.0.1:3000',
        exec,
      }),
    ).rejects.toThrow()

    const statuses = listSessionEvents(store, sessionId, 0)
      .map((e) => (JSON.parse(e.payload_json) as { status?: string }).status)
      .filter(Boolean)
    expect(statuses.some((s) => s!.startsWith('✗ Cloned repository'))).toBe(true)
    // Reap fired after the failure (an rm -f AFTER the run -d).
    const flat = calls.map((c) => c.join(' '))
    const runIdx = flat.findIndex((c) => c.startsWith('run -d'))
    expect(flat.slice(runIdx + 1).some((c) => c.startsWith('rm -f'))).toBe(true)
  })

  test('getContainerDiff parses branch/base/ahead/stat/diff from the container', async () => {
    const sessionId = makeSession()
    const store = getStore()
    // Launch so container_json (container name + repo) is recorded.
    await launchContainerSession(store, {
      sessionId,
      repoFullName: 'owner/demo',
      baseUrl: 'http://127.0.0.1:3000',
      proxyHealthy: async () => false,
      exec: fakeDocker().exec,
    })

    const fakeOut = [
      '@@BRANCH@@jarvis/add-vision',
      '@@BASE@@origin/main',
      '@@AHEAD@@1',
      '@@STAT@@',
      ' ulrich.py | 3 +++',
      ' 1 file changed, 3 insertions(+)',
      '@@DIFF@@',
      'diff --git a/ulrich.py b/ulrich.py',
      'new file mode 100644',
      '--- /dev/null',
      '+++ b/ulrich.py',
      '@@ -0,0 +1,3 @@',
      '+import cv2',
      '+',
      '+print("hi")',
    ].join('\n')
    const diffExec: DockerExec = async (args) =>
      args[0] === 'exec' && args.some((a) => a.includes('@@BRANCH@@'))
        ? { stdout: fakeOut, stderr: '' }
        : { stdout: '', stderr: '' }

    const result = await getContainerDiff(store, sessionId, diffExec)
    expect('error' in result).toBe(false)
    if ('error' in result) return
    expect(result.branch).toBe('jarvis/add-vision')
    expect(result.base).toBe('origin/main')
    expect(result.ahead).toBe(1)
    expect(result.stat).toContain('1 file changed, 3 insertions(+)')
    expect(result.diff).toContain('+import cv2')
    expect(result.diff).toContain('diff --git a/ulrich.py b/ulrich.py')
  })

  test('getContainerDiff returns an error when the session has no container', async () => {
    const sessionId = makeSession() // created, never launched → no container_json
    const store = getStore()
    const result = await getContainerDiff(store, sessionId, async () => ({
      stdout: '',
      stderr: '',
    }))
    expect('error' in result).toBe(true)
  })

  test('createContainerPR commits/pushes and returns the PR url from the container', async () => {
    const sessionId = makeSession()
    const store = getStore()
    await launchContainerSession(store, {
      sessionId,
      repoFullName: 'owner/demo',
      baseUrl: 'http://127.0.0.1:3000',
      proxyHealthy: async () => false,
      exec: fakeDocker().exec,
    })

    const prOut = '@@PRURL@@https://github.com/owner/demo/pull/7\n@@BRANCH@@jarvis/session-c0ffee00\n'
    const prExec: DockerExec = async (args) =>
      args[0] === 'exec' && args.some((a) => a.includes('gh pr create'))
        ? { stdout: prOut, stderr: '' }
        : { stdout: '', stderr: '' }

    const result = await createContainerPR(store, sessionId, prExec)
    expect('error' in result).toBe(false)
    if ('error' in result) return
    expect(result.url).toBe('https://github.com/owner/demo/pull/7')
    expect(result.branch).toBe('jarvis/session-c0ffee00')
  })

  test('applies environment config (env vars + setup script) to the container', async () => {
    const store = getStore()
    const env = createEnvironment(store, {
      machine_name: 'Cloud container',
      directory: '/workspace',
      git_repo_url: 'https://github.com/owner/demo',
      max_sessions: 4,
      worker_type: 'container',
      user_id: '00000000-0000-0000-0000-000000000001',
    })
    getOrCreateSession(store, 'abcdef0011223344', env.environment_id)
    setEnvironmentConfig(store, env.environment_id, {
      envVars: { MY_KEY: 'abc123' },
      setupScript: 'npm install',
      networkLevel: 'full',
      customAllowlist: [],
    })
    const { calls, exec } = fakeDocker()
    await launchContainerSession(store, {
      sessionId: 'abcdef0011223344',
      repoFullName: 'owner/demo',
      baseUrl: 'http://127.0.0.1:3000',
      proxyHealthy: async () => false,
      exec,
    })
    const flat = calls.map((c) => c.join(' '))
    expect(flat.find((c) => c.includes('cli.tsx'))!).toContain('MY_KEY=abc123')
    expect(flat.some((c) => c.includes('jarvis-env-setup.sh'))).toBe(true)
    const statuses = listSessionEvents(store, 'abcdef0011223344', 0)
      .map((e) => (JSON.parse(e.payload_json) as { status?: string }).status)
      .filter(Boolean)
    expect(statuses).toContain('✓ Run environment setup')
  })

  test('setup cache MISS: clones, runs setup, then commits a cache image', async () => {
    process.env.JARVIS_CODE_SETUP_CACHE = '1'
    const store = getStore()
    const env = createEnvironment(store, {
      machine_name: 'Cloud container',
      directory: '/workspace',
      git_repo_url: 'https://github.com/owner/demo',
      max_sessions: 4,
      worker_type: 'container',
      user_id: '00000000-0000-0000-0000-000000000001',
    })
    getOrCreateSession(store, 'cace000011223344', env.environment_id)
    setEnvironmentConfig(store, env.environment_id, {
      envVars: {},
      setupScript: 'npm install',
      networkLevel: 'full',
      customAllowlist: [],
    })
    const calls: string[][] = []
    const exec = async (args: string[]) => {
      calls.push(args)
      if (args[0] === 'image' && args[1] === 'inspect') throw new Error('no such image') // miss
      if (args.some((a) => a.includes('test -f'))) return { stdout: 'no\n', stderr: '' }
      return { stdout: '', stderr: '' }
    }
    await launchContainerSession(store, {
      sessionId: 'cace000011223344',
      repoFullName: 'owner/demo',
      baseUrl: 'http://127.0.0.1:3000',
      proxyHealthy: async () => false,
      exec,
    })
    const flat = calls.map((c) => c.join(' '))
    expect(flat.some((c) => c.includes('git clone'))).toBe(true)
    expect(flat.some((c) => c.startsWith('commit ') && c.includes('jarvis-workbench-cache:'))).toBe(true)
  })

  test('setup cache HIT: runs from the cache image, skips clone + setup + commit', async () => {
    process.env.JARVIS_CODE_SETUP_CACHE = '1'
    const store = getStore()
    const env = createEnvironment(store, {
      machine_name: 'Cloud container',
      directory: '/workspace',
      git_repo_url: 'https://github.com/owner/demo',
      max_sessions: 4,
      worker_type: 'container',
      user_id: '00000000-0000-0000-0000-000000000001',
    })
    getOrCreateSession(store, 'cace111122223333', env.environment_id)
    setEnvironmentConfig(store, env.environment_id, {
      envVars: {},
      setupScript: 'npm install',
      networkLevel: 'full',
      customAllowlist: [],
    })
    const calls: string[][] = []
    const exec = async (args: string[]) => {
      calls.push(args)
      if (args[0] === 'image' && args[1] === 'inspect') return { stdout: '', stderr: '' } // hit
      return { stdout: '', stderr: '' }
    }
    await launchContainerSession(store, {
      sessionId: 'cace111122223333',
      repoFullName: 'owner/demo',
      baseUrl: 'http://127.0.0.1:3000',
      proxyHealthy: async () => false,
      exec,
    })
    const flat = calls.map((c) => c.join(' '))
    expect(flat.some((c) => c.startsWith('run -d') && c.includes('jarvis-workbench-cache:'))).toBe(true)
    expect(flat.some((c) => c.includes('git clone'))).toBe(false)
    expect(flat.some((c) => c.startsWith('commit '))).toBe(false)
  })

  test('isolated network level wires a bridge network + egress proxy + host.docker.internal callback', async () => {
    const store = getStore()
    const env = createEnvironment(store, {
      machine_name: 'Cloud container',
      directory: '/workspace',
      git_repo_url: 'https://github.com/owner/demo',
      max_sessions: 4,
      worker_type: 'container',
      user_id: '00000000-0000-0000-0000-000000000001',
    })
    getOrCreateSession(store, 'e9e9000011223344', env.environment_id)
    setEnvironmentConfig(store, env.environment_id, {
      envVars: {},
      setupScript: '',
      networkLevel: 'trusted',
      customAllowlist: [],
    })
    const { calls, exec } = fakeDocker()
    await launchContainerSession(store, {
      sessionId: 'e9e9000011223344',
      repoFullName: 'owner/demo',
      baseUrl: 'http://127.0.0.1:3000',
      proxyHealthy: async () => false,
      exec,
    })
    const flat = calls.map((c) => c.join(' '))
    expect(flat.some((c) => c.startsWith('network create jarvis-net-e9e9000011223344'))).toBe(true)
    expect(
      flat.some(
        (c) => c.startsWith('run -d') && c.includes('jarvis-egress-e9e9000011223344') && c.includes('squid'),
      ),
    ).toBe(true)
    const wb = flat.find((c) => c.startsWith('run -d') && c.includes('jarvis-code-'))!
    expect(wb).toContain('--network jarvis-net-e9e9000011223344')
    expect(wb).toContain('--add-host=host.docker.internal:host-gateway')
    expect(wb).not.toContain('--network=host')
    const cli = flat.find((c) => c.includes('cli.tsx'))!
    expect(cli).toContain('host.docker.internal:3000')
    expect(cli).toContain('HTTP_PROXY=http://jarvis-egress-e9e9000011223344:3128')
  })

  test('default network level (full) keeps --network=host + 127.0.0.1 callback', async () => {
    const sessionId = makeSession()
    const store = getStore()
    const { calls, exec } = fakeDocker()
    await launchContainerSession(store, {
      sessionId,
      repoFullName: 'owner/demo',
      baseUrl: 'http://127.0.0.1:3000',
      proxyHealthy: async () => false,
      exec,
    })
    const flat = calls.map((c) => c.join(' '))
    expect(flat.some((c) => c.startsWith('run -d') && c.includes('--network=host'))).toBe(true)
    expect(flat.some((c) => c.startsWith('network create'))).toBe(false)
    expect(flat.find((c) => c.includes('cli.tsx'))!).toContain('127.0.0.1:3000')
  })

  test('createContainerPR draft mode passes --draft to gh', async () => {
    const sessionId = makeSession()
    const store = getStore()
    await launchContainerSession(store, {
      sessionId,
      repoFullName: 'owner/demo',
      baseUrl: 'http://127.0.0.1:3000',
      proxyHealthy: async () => false,
      exec: fakeDocker().exec,
    })
    let script = ''
    const exec: DockerExec = async (args) => {
      if (args[0] === 'exec' && args[2] === 'sh' && args[3] === '-c') script = args[4]
      return {
        stdout: '@@PRURL@@https://github.com/owner/demo/pull/9\n@@BRANCH@@jarvis/session-x\n',
        stderr: '',
      }
    }
    const r = await createContainerPR(store, sessionId, exec, 'draft')
    expect('error' in r).toBe(false)
    expect(script).toContain('gh pr create --fill --draft')
  })

  test('createContainerPR compose mode skips gh pr create + yields a compare URL', async () => {
    const sessionId = makeSession()
    const store = getStore()
    await launchContainerSession(store, {
      sessionId,
      repoFullName: 'owner/demo',
      baseUrl: 'http://127.0.0.1:3000',
      proxyHealthy: async () => false,
      exec: fakeDocker().exec,
    })
    let script = ''
    const exec: DockerExec = async (args) => {
      if (args[0] === 'exec' && args[2] === 'sh' && args[3] === '-c') script = args[4]
      return {
        stdout:
          '@@PRURL@@https://github.com/owner/demo/compare/main...jarvis/session-x?expand=1\n@@BRANCH@@jarvis/session-x\n',
        stderr: '',
      }
    }
    const r = await createContainerPR(store, sessionId, exec, 'compose')
    expect('error' in r).toBe(false)
    if ('error' in r) return
    expect(r.url).toContain('/compare/')
    expect(script).not.toContain('gh pr create')
    expect(script).toContain('/compare/')
  })

  test('injects enabled MCP connectors via --mcp-config', async () => {
    const sessionId = makeSession()
    const store = getStore()
    const { listMcpServers } = await import('@/lib/mcp/store')
    vi.mocked(listMcpServers).mockResolvedValueOnce([
      { id: 'sentry', name: 'sentry', url: 'https://mcp.sentry.dev', transport: 'http', enabled: true },
    ])
    const { calls, exec } = fakeDocker()
    await launchContainerSession(store, {
      sessionId,
      repoFullName: 'owner/demo',
      baseUrl: 'http://127.0.0.1:3000',
      proxyHealthy: async () => false,
      exec,
    })
    const flat = calls.map((c) => c.join(' '))
    expect(flat.some((c) => c.includes('/jarvis-config/.mcp.json'))).toBe(true)
    expect(flat.find((c) => c.includes('cli.tsx'))!).toContain('--mcp-config /jarvis-config/.mcp.json')
  })

  test('connectors: [] attaches none even when servers are enabled (per-session opt-in)', async () => {
    const sessionId = makeSession()
    const store = getStore()
    const { listMcpServers } = await import('@/lib/mcp/store')
    vi.mocked(listMcpServers).mockResolvedValueOnce([
      { id: 'sentry', name: 'sentry', url: 'https://mcp.sentry.dev', transport: 'http', enabled: true },
    ])
    const { calls, exec } = fakeDocker()
    await launchContainerSession(store, {
      sessionId,
      repoFullName: 'owner/demo',
      baseUrl: 'http://127.0.0.1:3000',
      proxyHealthy: async () => false,
      connectors: [],
      exec,
    })
    const flat = calls.map((c) => c.join(' '))
    expect(flat.some((c) => c.includes('cat > /jarvis-config/.mcp.json'))).toBe(false)
    expect(flat.find((c) => c.includes('cli.tsx'))!).not.toContain('--mcp-config')
  })

  test('connectors allow-list attaches only the picked subset', async () => {
    const sessionId = makeSession()
    const store = getStore()
    const { listMcpServers } = await import('@/lib/mcp/store')
    vi.mocked(listMcpServers).mockResolvedValueOnce([
      { id: 'sentry', name: 'sentry', url: 'https://mcp.sentry.dev', transport: 'http', enabled: true },
      { id: 'linear', name: 'linear', url: 'https://mcp.linear.app', transport: 'http', enabled: true },
    ])
    const { calls, exec } = fakeDocker()
    await launchContainerSession(store, {
      sessionId,
      repoFullName: 'owner/demo',
      baseUrl: 'http://127.0.0.1:3000',
      proxyHealthy: async () => false,
      connectors: ['sentry'],
      exec,
    })
    const flat = calls.map((c) => c.join(' '))
    const mcpWrite = flat.find((c) => c.includes('cat > /jarvis-config/.mcp.json'))!
    expect(mcpWrite).toContain('sentry')
    expect(mcpWrite).not.toContain('linear')
    expect(flat.find((c) => c.includes('cli.tsx'))!).toContain('--mcp-config /jarvis-config/.mcp.json')
  })

  test('multi-repo: clones each extra repo alongside the primary', async () => {
    const sessionId = makeSession()
    const store = getStore()
    const { calls, exec } = fakeDocker()
    await launchContainerSession(store, {
      sessionId,
      repoFullName: 'owner/demo',
      baseUrl: 'http://127.0.0.1:3000',
      proxyHealthy: async () => false,
      extraRepos: ['owner/lib', 'owner/shared'],
      exec,
    })
    const flat = calls.map((c) => c.join(' '))
    expect(flat.some((c) => c.includes('git clone') && c.includes('owner/lib'))).toBe(true)
    expect(flat.some((c) => c.includes('git clone') && c.includes('owner/shared'))).toBe(true)
  })

  test('mergeContainerPR squash-merges via gh', async () => {
    const sessionId = makeSession()
    const store = getStore()
    await launchContainerSession(store, {
      sessionId,
      repoFullName: 'owner/demo',
      baseUrl: 'http://127.0.0.1:3000',
      proxyHealthy: async () => false,
      exec: fakeDocker().exec,
    })
    let script = ''
    const exec: DockerExec = async (args) => {
      if (args[0] === 'exec' && args[2] === 'sh' && args[3] === '-c') script = args[4]
      return { stdout: '@@MERGED@@1\n', stderr: '' }
    }
    const r = await mergeContainerPR(store, sessionId, exec)
    expect('merged' in r).toBe(true)
    expect(script).toContain('gh pr merge')
  })

  test('stopContainerSession removes the recorded container', async () => {
    const sessionId = makeSession()
    const store = getStore()
    const launch = fakeDocker()
    await launchContainerSession(store, {
      sessionId,
      repoFullName: 'owner/demo',
      baseUrl: 'http://127.0.0.1:3000',
      exec: launch.exec,
    })
    const stop = fakeDocker()
    await stopContainerSession(store, sessionId, stop.exec)
    expect(stop.calls).toEqual([
      ['rm', '-f', containerNameFor(sessionId)],
      ['rm', '-f', `jarvis-egress-${sessionId}`],
      ['network', 'rm', `jarvis-net-${sessionId}`],
    ])
  })
})

describe('plan route', () => {
  test('extracts the latest ExitPlanMode plan from session events', async () => {
    const store = getStore()
    const sessionId = makeSession()
    appendSessionEvent(store, sessionId, {
      type: 'assistant',
      payload: {
        message: {
          content: [
            { type: 'text', text: 'Here is the plan' },
            { type: 'tool_use', name: 'ExitPlanMode', input: { plan: 'Step 1\nStep 2' } },
          ],
        },
      },
    })
    const route = await import('@/app/api/bridge/v1/sessions/[sessionId]/plan/route')
    const res = await route.GET(new Request('http://127.0.0.1:3000/x'), {
      params: Promise.resolve({ sessionId }),
    })
    const j = (await res.json()) as { plan: string }
    expect(j.plan).toBe('Step 1\nStep 2')
  })

  test('returns an empty plan when no ExitPlanMode event exists', async () => {
    const sessionId = makeSession()
    const route = await import('@/app/api/bridge/v1/sessions/[sessionId]/plan/route')
    const res = await route.GET(new Request('http://127.0.0.1:3000/x'), {
      params: Promise.resolve({ sessionId }),
    })
    const j = (await res.json()) as { plan: string }
    expect(j.plan).toBe('')
  })
})

describe('validRepoFullName', () => {
  test('accepts owner/name and rejects path tricks', () => {
    expect(validRepoFullName('owner/repo')).toBe(true)
    expect(validRepoFullName('owner/repo.name-x_1')).toBe(true)
    expect(validRepoFullName('owner')).toBe(false)
    expect(validRepoFullName('owner/repo/extra')).toBe(false)
    expect(validRepoFullName('owner/../etc')).toBe(false)
    expect(validRepoFullName('owner/re po')).toBe(false)
  })
})

describe('tasks route container branch', () => {
  test('container env → no work enqueued, launch called with the repo', async () => {
    const containers = await import('@/lib/bridge/containers')
    const launchSpy = vi
      .spyOn(containers, 'launchContainerSession')
      .mockResolvedValue(undefined)

    const store = getStore()
    const env = createEnvironment(store, {
      machine_name: 'Cloud container',
      directory: '/workspace',
      git_repo_url: 'https://github.com/owner/demo',
      max_sessions: 4,
      worker_type: 'container',
      user_id: '00000000-0000-0000-0000-000000000001',
    })

    const tasks = await import('@/app/api/bridge/v1/tasks/route')
    const res = await tasks.POST(
      new Request('http://127.0.0.1:3000/api/bridge/v1/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          environment_id: env.environment_id,
          prompt: 'containerize me',
        }),
      }),
    )
    expect(res.status).toBe(200)
    const { session_id } = (await res.json()) as { session_id: string }
    expect(session_id).toBeTruthy()

    expect(launchSpy).toHaveBeenCalledTimes(1)
    expect(launchSpy.mock.calls[0][1]).toMatchObject({
      sessionId: session_id,
      repoFullName: 'owner/demo',
      baseUrl: 'http://127.0.0.1:3000',
    })

    // No bridge work was enqueued — the web manages the container itself.
    const work = store.db.prepare('SELECT count(*) AS n FROM work').get() as {
      n: number
    }
    expect(work.n).toBe(0)

    // The prompt is still seeded for SSE catch-up.
    const inbound = listInboundSince(store, session_id, 0)
    expect(inbound.length).toBe(1)
    launchSpy.mockRestore()
  })
})

describe('resumeContainerWorker (auto-resume on reopen)', () => {
  async function launchOnce(sessionId: string): Promise<void> {
    const { exec } = fakeDocker()
    await launchContainerSession(getStore(), {
      sessionId,
      repoFullName: 'owner/demo',
      baseUrl: 'http://127.0.0.1:3000',
      exec,
      proxyHealthy: async () => false,
    })
  }

  test('re-execs the persisted worker spec into a running container, fencing the epoch', async () => {
    const store = getStore()
    const sessionId = makeSession()
    await launchOnce(sessionId)
    const spec = getWorkerSpec(store, sessionId)
    expect(spec).not.toBeNull()
    const epochBefore = findSession(store, sessionId)!.worker_epoch
    // Scenario: one COMPLETED turn (inbound A @ t=1000, then a result @ t=2000),
    // then a PENDING message the user sent while the worker was down (inbound B,
    // now). Resume must floor at A — skipping the finished turn (no re-run) while
    // leaving B deliverable so it still gets answered.
    const insInbound = store.db.prepare(
      'INSERT INTO session_inbound (session_id, payload_json, created_at) VALUES (?, ?, ?)',
    )
    const seqA = Number(
      insInbound.run(sessionId, JSON.stringify({ type: 'user' }), 1000).lastInsertRowid,
    )
    store.db
      .prepare(
        'INSERT INTO session_events (event_id, session_id, type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)',
      )
      .run('evt-result', sessionId, 'result', JSON.stringify({ type: 'result' }), 2000)
    const seqB = appendInbound(store, sessionId, { type: 'user', message: { role: 'user', content: 'pending while down' } })
    expect(seqB).toBeGreaterThan(seqA)
    expect(getInboundFloorSeq(store, sessionId)).toBe(0) // not raised before resume

    const calls: string[][] = []
    const resumeExec: DockerExec = async (args) => {
      calls.push(args)
      if (args[0] === 'inspect') return { stdout: 'true\n', stderr: '' } // running
      if (args.some((a) => a.includes('awk'))) return { stdout: '0\n', stderr: '' } // worker dead (0 live)
      return { stdout: '', stderr: '' }
    }
    expect(await resumeContainerWorker(store, sessionId, resumeExec)).toBe(true)

    // It detached-exec'd the SAME CLI command into the SAME workdir (so the CLI
    // resumes its own session rather than starting over).
    const relaunch = calls.find(
      (a) => a[0] === 'exec' && a.includes('-d') && a.includes('sh'),
    )
    expect(relaunch).toBeDefined()
    expect(relaunch![3]).toBe(spec!.workdir)
    const cmd = relaunch![relaunch!.length - 1]
    expect(cmd).toContain('cli.tsx')
    expect(cmd).toContain(`--session-id '${sessionId}'`)
    // Epoch bumped + reflected in the relaunch env (fences any stale worker).
    const epochAfter = findSession(store, sessionId)!.worker_epoch
    expect(epochAfter).toBeGreaterThan(epochBefore)
    expect(relaunch).toContain(`CLAUDE_CODE_WORKER_EPOCH=${epochAfter}`)
    // Floor = the completed turn's inbound (A), NOT the tip — so the finished
    // turn isn't replayed, but the pending message B (seq > A) is still
    // delivered and answered.
    expect(getInboundFloorSeq(store, sessionId)).toBe(seqA)
  })

  test('no-ops when a worker is already alive', async () => {
    const store = getStore()
    const sessionId = makeSession()
    await launchOnce(sessionId)
    const aliveExec: DockerExec = async (args) => {
      if (args[0] === 'inspect') return { stdout: 'true\n', stderr: '' }
      if (args.some((a) => a.includes('awk'))) return { stdout: '1\n', stderr: '' } // 1 live worker
      return { stdout: '', stderr: '' }
    }
    expect(await resumeContainerWorker(store, sessionId, aliveExec)).toBe(false)
  })

  test('returns false with no spec, and when the container is gone', async () => {
    const store = getStore()
    const sessionId = makeSession()
    // No launch → no persisted worker spec.
    expect(
      await resumeContainerWorker(store, sessionId, async () => ({ stdout: '', stderr: '' })),
    ).toBe(false)
    // Spec exists but the container isn't running → nothing to resume into.
    await launchOnce(sessionId)
    const goneExec: DockerExec = async (args) =>
      args[0] === 'inspect'
        ? { stdout: 'false\n', stderr: '' }
        : { stdout: '', stderr: '' }
    expect(await resumeContainerWorker(store, sessionId, goneExec)).toBe(false)
  })
})

describe('runOrphanContainerSweep', () => {
  // A labeled-docker fake: `ps` lists the given container names; `inspect`
  // returns each container's StartedAt (default 1h ago = old); rm/network ok.
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
  function makeEnv() {
    return createEnvironment(getStore(), {
      machine_name: 'Cloud container',
      directory: '/workspace',
      git_repo_url: 'https://github.com/owner/demo',
      max_sessions: 4,
      worker_type: 'container',
      user_id: '00000000-0000-0000-0000-000000000001',
    })
  }

  test('reaps untracked + archived containers, spares tracked + freshly-launched', async () => {
    const store = getStore()
    const env = makeEnv()
    // tracked + live → DB-driven reclaim owns it; sweep must SKIP (and not even
    // pay for an inspect).
    getOrCreateSession(store, 'aaaaaaaaaaaaaaaa', env.environment_id)
    setSessionContainer(store, 'aaaaaaaaaaaaaaaa', {
      container: containerNameFor('aaaaaaaaaaaaaaaa'),
      repo: 'owner/demo',
    })
    // archived → DB-driven reclaim skips archived rows; sweep must REAP.
    getOrCreateSession(store, 'bbbbbbbbbbbbbbbb', env.environment_id)
    setSessionContainer(store, 'bbbbbbbbbbbbbbbb', {
      container: containerNameFor('bbbbbbbbbbbbbbbb'),
      repo: 'owner/demo',
    })
    archiveSession(store, 'bbbbbbbbbbbbbbbb')
    // 'cccc…' = deleted session (no row) → orphan → REAP.
    // 'dddd…' = no row but started just now → race-guard SKIP.
    const names = [
      containerNameFor('aaaaaaaaaaaaaaaa'),
      containerNameFor('bbbbbbbbbbbbbbbb'),
      containerNameFor('cccccccccccccccc'),
      containerNameFor('dddddddddddddddd'),
    ]
    const { calls, exec } = sweepDocker(names, {
      [containerNameFor('dddddddddddddddd')]: new Date().toISOString(),
    })
    const reaped = await runOrphanContainerSweep(store, exec)
    expect(reaped).toBe(2)
    const rm = removed(calls)
    expect(rm).toContain(containerNameFor('bbbbbbbbbbbbbbbb'))
    expect(rm).toContain(containerNameFor('cccccccccccccccc'))
    expect(rm).not.toContain(containerNameFor('aaaaaaaaaaaaaaaa'))
    expect(rm).not.toContain(containerNameFor('dddddddddddddddd'))
    // tracked-live is skipped before the inspect probe.
    expect(
      calls.some(
        (c) => c[0] === 'inspect' && c.includes(containerNameFor('aaaaaaaaaaaaaaaa')),
      ),
    ).toBe(false)
  })

  test('returns 0 when docker is unavailable', async () => {
    const exec: DockerExec = async () => {
      throw new Error('docker missing')
    }
    expect(await runOrphanContainerSweep(getStore(), exec)).toBe(0)
  })

  test('ignores containers without our jarvis-code- prefix', async () => {
    const { calls, exec } = sweepDocker(['some-other-container', 'jarvis-ws-abc'])
    expect(await runOrphanContainerSweep(getStore(), exec)).toBe(0)
    expect(removed(calls)).toHaveLength(0)
  })
})
