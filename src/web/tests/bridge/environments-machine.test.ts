import { describe, expect, test, beforeEach } from 'vitest'
import { _resetForTests, getStore } from '@/lib/bridge/db'
import { createEnvironment, listEnvironments } from '@/lib/bridge/store'

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
