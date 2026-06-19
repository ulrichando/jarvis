import { describe, expect, test, beforeEach, afterEach, vi } from 'vitest'
import { _resetForTests, getStore } from '@/lib/bridge/db'
import {
  getOrCreateSession,
  createEnvironment,
  findSession,
  getSessionGitScope,
} from '@/lib/bridge/store'
import { launchContainerSession, type DockerExec } from '@/lib/bridge/containers'

vi.mock('@/lib/auth-helpers', () => ({ getUserId: async () => '00000000-0000-0000-0000-000000000001' }))
// Even with a real PAT available host-side, it must NEVER reach the container.
vi.mock('@/lib/connectors/github', () => ({
  getGithubToken: async () => 'ghp_REAL_SECRET',
  githubStatus: async () => ({ connected: true, login: 'tester' }),
}))
vi.mock('@/lib/mcp/store', () => ({ listMcpServers: vi.fn(async () => []) }))

function fakeDocker() {
  const calls: string[][] = []
  const exec: DockerExec = async (args) => {
    calls.push(args)
    if (args.some((a) => a.includes('test -f'))) return { stdout: 'no\n', stderr: '' }
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

beforeEach(() => {
  _resetForTests()
  vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('no proxy in tests') }))
})
afterEach(() => vi.unstubAllGlobals())

describe('launch keeps the real PAT out of the container', () => {
  test('clone uses the proxy URL, no GH_TOKEN, scope persisted', async () => {
    const sid = makeSession()
    const { calls, exec } = fakeDocker()
    await launchContainerSession(getStore(), {
      sessionId: sid,
      repoFullName: 'owner/demo',
      baseUrl: 'http://127.0.0.1:3000',
      exec,
    })

    const flat = calls.map((c) => c.join(' '))
    // The real secret appears in NO docker command line.
    expect(flat.some((c) => c.includes('ghp_REAL_SECRET'))).toBe(false)
    // The clone targets the per-session proxy route, not github.com.
    const clone = calls.find((c) => c[2] === 'git' && c[3] === 'clone')
    expect(clone?.[4]).toContain(`/api/bridge/v1/code/sessions/${sid}/git/owner/demo.git`)
    expect(clone?.[4]).not.toContain('github.com')
    // No GH_TOKEN / GITHUB_TOKEN injected into the worker.
    expect(flat.some((c) => /GH_TOKEN=|GITHUB_TOKEN=/.test(c))).toBe(false)
    // The git scope is persisted for the proxy to enforce.
    expect(getSessionGitScope(findSession(getStore(), sid)!)).toEqual(['owner/demo'])
  })
})
