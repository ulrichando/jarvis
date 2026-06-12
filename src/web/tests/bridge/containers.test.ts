import { describe, expect, test, beforeEach, vi } from 'vitest'
import { _resetForTests, getStore } from '@/lib/bridge/db'
import {
  getOrCreateSession,
  createEnvironment,
  findSession,
  listSessionEvents,
  listInboundSince,
} from '@/lib/bridge/store'
import {
  launchContainerSession,
  stopContainerSession,
  containerNameFor,
  validRepoFullName,
  type DockerExec,
} from '@/lib/bridge/containers'

vi.mock('@/lib/auth-helpers', () => ({
  getUserId: async () => '00000000-0000-0000-0000-000000000001',
}))
vi.mock('@/lib/connectors/github', () => ({
  getGithubToken: async () => 'ghp_test_token',
  githubStatus: async () => ({ connected: true, login: 'tester' }),
}))

beforeEach(() => {
  _resetForTests()
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
      '✓ Started Claude Code',
    ])
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
    expect(stop.calls).toEqual([['rm', '-f', containerNameFor(sessionId)]])
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
