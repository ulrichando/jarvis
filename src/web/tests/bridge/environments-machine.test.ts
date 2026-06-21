import { describe, expect, test, beforeEach, vi } from 'vitest'
import { _resetForTests, getStore } from '@/lib/bridge/db'
import {
  createEnvironment,
  listEnvironments,
  reapStaleSandboxes,
  isEnvironmentOnline,
  SANDBOX_TTL_MS,
  ONLINE_TTL_MS,
} from '@/lib/bridge/store'

// The route resolves the caller via better-auth; tests have no cookie → pin to
// LOCAL_USER_ID (the same getUserId fallback + the owner the test envs use).
vi.mock('@/lib/auth-helpers', () => ({
  getUserId: async () => '00000000-0000-0000-0000-000000000001',
}))

const USER = '00000000-0000-0000-0000-000000000001'

beforeEach(() => {
  _resetForTests()
})

describe('machine identity', () => {
  test('same machine, two directories → one row', () => {
    const store = getStore()
    const a = createEnvironment(store, {
      machine_name: 'Moon',
      directory: '/repo/a',
      max_sessions: 4,
      worker_type: 'claude_code_repl',
      user_id: USER,
    })
    const b = createEnvironment(store, {
      machine_name: 'Moon',
      directory: '/repo/b',
      max_sessions: 4,
      worker_type: 'claude_code_repl',
      user_id: USER,
    })
    expect(b.environment_id).toBe(a.environment_id)
    expect(
      listEnvironments(store, USER).filter((e) => e.worker_type !== 'container'),
    ).toHaveLength(1)
  })

  test('two containers stay separate', () => {
    const store = getStore()
    const a = createEnvironment(store, {
      machine_name: 'Cloud container',
      directory: '/workspace',
      max_sessions: 4,
      worker_type: 'container',
      user_id: USER,
    })
    const b = createEnvironment(store, {
      machine_name: 'Cloud container',
      directory: '/workspace',
      max_sessions: 4,
      worker_type: 'container',
      user_id: USER,
    })
    expect(b.environment_id).not.toBe(a.environment_id)
  })
})

describe('reaper + online', () => {
  test('reaps stale per-repo sandbox; keeps machine, fresh, busy, and repo-less config env', () => {
    const store = getStore()
    const now = Date.now()
    const repo = 'https://github.com/o/r'
    const stale = createEnvironment(store, { machine_name: 'Cloud container', directory: '/workspace', git_repo_url: repo, max_sessions: 4, worker_type: 'container', user_id: USER })
    const fresh = createEnvironment(store, { machine_name: 'Cloud container', directory: '/workspace', git_repo_url: repo, max_sessions: 4, worker_type: 'container', user_id: USER })
    const busy = createEnvironment(store, { machine_name: 'Cloud container', directory: '/workspace', git_repo_url: repo, max_sessions: 4, worker_type: 'container', user_id: USER })
    const machine = createEnvironment(store, { machine_name: 'Moon', directory: '/repo', max_sessions: 4, worker_type: 'claude_code_repl', user_id: USER })
    // persistent repo-less "Default" cloud env — must NOT be reaped even when stale
    const dflt = createEnvironment(store, { machine_name: 'Default', directory: '/workspace', max_sessions: 4, worker_type: 'container', user_id: USER })

    // age `stale`, `busy`, and `dflt` past the TTL; give `busy` an active session
    store.db
      .prepare('UPDATE environments SET last_seen_at = ? WHERE environment_id IN (?, ?, ?)')
      .run(now - SANDBOX_TTL_MS - 1000, stale.environment_id, busy.environment_id, dflt.environment_id)
    store.db
      .prepare('INSERT INTO sessions (session_id, environment_id, archived, created_at) VALUES (?, ?, 0, ?)')
      .run('s_busy', busy.environment_id, now)

    const reaped = reapStaleSandboxes(store, now)
    expect(reaped).toBe(1) // only `stale` (a per-repo sandbox)
    const ids = listEnvironments(store, USER).map((e) => e.environment_id)
    expect(ids).not.toContain(stale.environment_id)
    expect(ids).toContain(fresh.environment_id)
    expect(ids).toContain(busy.environment_id) // spared: active session
    expect(ids).toContain(machine.environment_id) // never reaped (not container)
    expect(ids).toContain(dflt.environment_id) // spared: repo-less config env
  })

  test('isEnvironmentOnline reflects last_seen', () => {
    const now = Date.now()
    const base = {
      environment_id: 'e', environment_secret: 's', machine_name: 'Moon', directory: '/r',
      branch: null, git_repo_url: null, max_sessions: 4, worker_type: 'claude_code_repl',
      user_id: USER, created_at: now, config_json: null,
    }
    expect(isEnvironmentOnline({ ...base, last_seen_at: now - 1000 }, now)).toBe(true)
    expect(isEnvironmentOnline({ ...base, last_seen_at: now - ONLINE_TTL_MS - 1000 }, now)).toBe(false)
  })
})

describe('GET /environments', () => {
  test('ensures a Default cloud env, reaps stale sandbox, returns online flag', async () => {
    const store = getStore()
    const now = Date.now()
    const stale = createEnvironment(store, { machine_name: 'Cloud container', directory: '/workspace', git_repo_url: 'https://github.com/o/r', max_sessions: 4, worker_type: 'container', user_id: USER })
    createEnvironment(store, { machine_name: 'Moon', directory: '/repo', max_sessions: 4, worker_type: 'claude_code_repl', user_id: USER })
    store.db
      .prepare('UPDATE environments SET last_seen_at = ? WHERE environment_id = ?')
      .run(now - SANDBOX_TTL_MS - 1000, stale.environment_id)

    const { GET } = await import('@/app/api/bridge/v1/environments/route')
    const res = await GET(new Request('http://127.0.0.1:3000/api/bridge/v1/environments'))
    const body = (await res.json()) as {
      environments: Array<{ machine_name: string; online: boolean; worker_type: string }>
    }

    // stale per-repo sandbox reaped; Moon + an auto-ensured Default cloud env remain
    const names = body.environments.map((e) => e.machine_name)
    expect(names).toContain('Moon')
    expect(names).toContain('Default')
    expect(names).not.toContain('Cloud container')
    expect(body.environments.find((e) => e.machine_name === 'Moon')?.online).toBe(true)
  })
})
