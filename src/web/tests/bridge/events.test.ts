import { describe, expect, test } from 'vitest'
import { emitWorkAvailable, waitForWork } from '@/lib/bridge/events'

describe('event bus', () => {
  test('waitForWork resolves true when emitWorkAvailable fires before timeout', async () => {
    // Unique env_id per test — the bus is a module singleton.
    const id = 'env-emit-wins'
    const promise = waitForWork(id, 1000)
    setTimeout(() => emitWorkAvailable(id), 50)
    expect(await promise).toBe(true)
  })

  test('waitForWork resolves false on timeout', async () => {
    expect(await waitForWork('env-timeout', 100)).toBe(false)
  })

  test('events are scoped to env_id', async () => {
    const promise = waitForWork('env-scope-wait', 200)
    emitWorkAvailable('env-scope-other') // wrong env, should not wake the wait
    expect(await promise).toBe(false)
  })
})
