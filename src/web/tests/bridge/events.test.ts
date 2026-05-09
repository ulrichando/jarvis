import { describe, expect, test } from 'vitest'
import { emitWorkAvailable, waitForWork } from '@/lib/bridge/events'

describe('event bus', () => {
  test('waitForWork resolves true when emitWorkAvailable fires before timeout', async () => {
    const promise = waitForWork('env1', 1000)
    setTimeout(() => emitWorkAvailable('env1'), 50)
    expect(await promise).toBe(true)
  })

  test('waitForWork resolves false on timeout', async () => {
    expect(await waitForWork('envX', 100)).toBe(false)
  })

  test('events are scoped to env_id', async () => {
    const promise = waitForWork('env1', 200)
    emitWorkAvailable('env2') // wrong env, should not wake env1
    expect(await promise).toBe(false)
  })
})
