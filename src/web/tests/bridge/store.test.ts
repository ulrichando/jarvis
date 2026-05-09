import { describe, expect, test, beforeEach } from 'vitest'
import Database from 'better-sqlite3'
import {
  initSchema,
  createEnvironment,
  findEnvironment,
  enqueueWork,
  leaseNextWork,
  reclaimExpiredLeases,
  heartbeatWork,
  stopWork,
  appendSessionEvent,
  archiveSession,
  deleteEnvironment,
  validateEnvSecret,
  type Store,
} from '@/lib/bridge/store'

let store: Store

beforeEach(() => {
  const db = new Database(':memory:')
  initSchema(db)
  store = { db }
})

describe('environments', () => {
  test('createEnvironment generates id + secret', () => {
    const env = createEnvironment(store, {
      machine_name: 'kali',
      directory: '/tmp',
      max_sessions: 4,
      worker_type: 'jarvis',
    })
    expect(env.environment_id).toBeTruthy()
    expect(env.environment_secret).toBeTruthy()
    expect(env.environment_secret.length).toBeGreaterThan(20)
  })

  test('createEnvironment with reuse_id reattaches when id exists', () => {
    const env1 = createEnvironment(store, {
      machine_name: 'kali',
      directory: '/tmp',
      max_sessions: 4,
      worker_type: 'jarvis',
    })
    const env2 = createEnvironment(store, {
      machine_name: 'kali',
      directory: '/tmp',
      max_sessions: 4,
      worker_type: 'jarvis',
      reuse_id: env1.environment_id,
    })
    expect(env2.environment_id).toBe(env1.environment_id)
    expect(env2.environment_secret).toBe(env1.environment_secret)
  })

  test('createEnvironment with unknown reuse_id creates fresh', () => {
    const env = createEnvironment(store, {
      machine_name: 'kali',
      directory: '/tmp',
      max_sessions: 4,
      worker_type: 'jarvis',
      reuse_id: 'nonexistent',
    })
    expect(env.environment_id).not.toBe('nonexistent')
  })

  test('validateEnvSecret returns true on match', () => {
    const env = createEnvironment(store, {
      machine_name: 'kali',
      directory: '/tmp',
      max_sessions: 4,
      worker_type: 'jarvis',
    })
    expect(
      validateEnvSecret(store, env.environment_id, env.environment_secret),
    ).toBe(true)
    expect(validateEnvSecret(store, env.environment_id, 'wrong')).toBe(false)
    expect(validateEnvSecret(store, 'nonexistent', 'x')).toBe(false)
  })

  test('deleteEnvironment cascades work', () => {
    const env = createEnvironment(store, {
      machine_name: 'kali',
      directory: '/tmp',
      max_sessions: 4,
      worker_type: 'jarvis',
    })
    enqueueWork(store, env.environment_id, {
      session_id: 'sess1',
      data: { prompt: 'x' },
    })
    deleteEnvironment(store, env.environment_id)
    expect(findEnvironment(store, env.environment_id)).toBeNull()
    const w = leaseNextWork(store, env.environment_id, 60_000)
    expect(w).toBeNull()
  })
})

describe('work queue', () => {
  let envId: string

  beforeEach(() => {
    const env = createEnvironment(store, {
      machine_name: 'kali',
      directory: '/tmp',
      max_sessions: 4,
      worker_type: 'jarvis',
    })
    envId = env.environment_id
  })

  test('leaseNextWork returns null when queue empty', () => {
    expect(leaseNextWork(store, envId, 60_000)).toBeNull()
  })

  test('leaseNextWork returns oldest pending and marks leased', () => {
    enqueueWork(store, envId, { session_id: 'a', data: { n: 1 } })
    enqueueWork(store, envId, { session_id: 'b', data: { n: 2 } })
    const w1 = leaseNextWork(store, envId, 60_000)
    expect(w1).not.toBeNull()
    expect(w1!.state).toBe('leased')
    // Second lease should pick up the second item, not re-lease w1.
    const w2 = leaseNextWork(store, envId, 60_000)
    expect(w2).not.toBeNull()
    expect(w2!.id).not.toBe(w1!.id)
  })

  test('reclaimExpiredLeases marks expired work pending again', () => {
    enqueueWork(store, envId, { session_id: 'a', data: { n: 1 } })
    leaseNextWork(store, envId, -1_000) // immediately expired
    const reclaimed = reclaimExpiredLeases(store, envId)
    expect(reclaimed).toBe(1)
    const w = leaseNextWork(store, envId, 60_000)
    expect(w).not.toBeNull() // available again
  })

  test('heartbeatWork extends lease', () => {
    enqueueWork(store, envId, { session_id: 'a', data: {} })
    const w = leaseNextWork(store, envId, 60_000)!
    const before = w.lease_expires_at!
    // Heartbeat with a longer TTL — the new expiry must be > before.
    const result = heartbeatWork(store, envId, w.id, 120_000)
    expect(result.lease_extended).toBe(true)
    expect(result.state).toBe('leased')
    const refreshed = store.db
      .prepare('SELECT lease_expires_at FROM work WHERE id = ?')
      .get(w.id) as { lease_expires_at: number }
    expect(refreshed.lease_expires_at).toBeGreaterThan(before)
    // Lease still held, so a fresh leaseNextWork returns null.
    const next = leaseNextWork(store, envId, 60_000)
    expect(next).toBeNull()
  })

  test('heartbeatWork rejects stopped work', () => {
    enqueueWork(store, envId, { session_id: 'a', data: {} })
    const w = leaseNextWork(store, envId, 60_000)!
    stopWork(store, envId, w.id)
    const result = heartbeatWork(store, envId, w.id, 60_000)
    expect(result.lease_extended).toBe(false)
  })

  test('stopWork marks work stopped', () => {
    enqueueWork(store, envId, { session_id: 'a', data: {} })
    const w = leaseNextWork(store, envId, 60_000)!
    stopWork(store, envId, w.id)
    // Subsequent heartbeat should fail.
    expect(heartbeatWork(store, envId, w.id, 60_000).lease_extended).toBe(false)
  })
})

describe('sessions', () => {
  test('appendSessionEvent persists event', () => {
    appendSessionEvent(store, 'sess1', {
      type: 'permission_response',
      payload: { granted: true },
    })
    const events = store.db
      .prepare('SELECT * FROM session_events WHERE session_id = ?')
      .all('sess1')
    expect(events).toHaveLength(1)
  })

  test('archiveSession returns "archived" first time, "already" after', () => {
    expect(archiveSession(store, 'sess1')).toBe('archived')
    expect(archiveSession(store, 'sess1')).toBe('already')
  })
})
