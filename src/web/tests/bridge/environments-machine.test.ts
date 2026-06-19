import { describe, expect, test, beforeEach } from 'vitest'
import { _resetForTests, getStore } from '@/lib/bridge/db'
import {
  createEnvironment,
  listEnvironments,
  reapStaleSandboxes,
  isEnvironmentOnline,
  SANDBOX_TTL_MS,
  ONLINE_TTL_MS,
} from '@/lib/bridge/store'

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
  test('reaps stale container, keeps machine + fresh sandbox + active-session sandbox', () => {
    const store = getStore()
    const now = Date.now()
    const stale = createEnvironment(store, { machine_name: 'Cloud container', directory: '/workspace', max_sessions: 4, worker_type: 'container', user_id: USER })
    const fresh = createEnvironment(store, { machine_name: 'Cloud container', directory: '/workspace', max_sessions: 4, worker_type: 'container', user_id: USER })
    const busy = createEnvironment(store, { machine_name: 'Cloud container', directory: '/workspace', max_sessions: 4, worker_type: 'container', user_id: USER })
    const machine = createEnvironment(store, { machine_name: 'Moon', directory: '/repo', max_sessions: 4, worker_type: 'claude_code_repl', user_id: USER })

    // age `stale` and `busy` past the TTL; give `busy` an active session
    store.db
      .prepare('UPDATE environments SET last_seen_at = ? WHERE environment_id IN (?, ?)')
      .run(now - SANDBOX_TTL_MS - 1000, stale.environment_id, busy.environment_id)
    store.db
      .prepare('INSERT INTO sessions (session_id, environment_id, archived, created_at) VALUES (?, ?, 0, ?)')
      .run('s_busy', busy.environment_id, now)

    const reaped = reapStaleSandboxes(store, now)
    expect(reaped).toBe(1) // only `stale`
    const ids = listEnvironments(store, USER).map((e) => e.environment_id)
    expect(ids).not.toContain(stale.environment_id)
    expect(ids).toContain(fresh.environment_id)
    expect(ids).toContain(busy.environment_id) // spared: active session
    expect(ids).toContain(machine.environment_id) // never reaped (not container)
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
