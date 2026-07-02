import { expect, test } from 'bun:test'
import { ConcurrencyLimiter, computeConcurrency, TOTAL_AGENT_CAP } from './limiter.js'

test('computeConcurrency = min(16, cores-2), floor 1', () => {
  expect(computeConcurrency(4)).toBe(2)
  expect(computeConcurrency(64)).toBe(16)
  expect(computeConcurrency(1)).toBe(1)
})

test('never runs more than `max` at once', async () => {
  const lim = new ConcurrencyLimiter(2)
  let active = 0
  let peak = 0
  const task = () => lim.run(async () => {
    active++; peak = Math.max(peak, active)
    await new Promise(r => setTimeout(r, 10))
    active--
  })
  await Promise.all([task(), task(), task(), task(), task()])
  expect(peak).toBeLessThanOrEqual(2)
})

test('total cap throws past TOTAL_AGENT_CAP', async () => {
  const lim = new ConcurrencyLimiter(4)
  lim._forceCount(TOTAL_AGENT_CAP)
  await expect(lim.run(async () => 1)).rejects.toThrow(/agent cap|1000/)
})
