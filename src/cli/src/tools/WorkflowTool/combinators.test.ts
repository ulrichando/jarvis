import { expect, test } from 'bun:test'
import { runParallel, runPipeline, MAX_ITEMS } from './combinators.js'

test('parallel awaits all, null on throw, never rejects', async () => {
  const r = await runParallel([
    async () => 1,
    async () => { throw new Error('boom') },
    async () => 3,
  ])
  expect(r).toEqual([1, null, 3])
})

test('pipeline chains stages per item, passes (prev, item, index)', async () => {
  const seen: Array<[unknown, unknown, number]> = []
  const r = await runPipeline(
    ['a', 'b'],
    async (item: string) => item.toUpperCase(),
    async (prev: string, item: string, i: number) => {
      seen.push([prev, item, i])
      return `${prev}-${item}-${i}`
    },
  )
  expect(r).toEqual(['A-a-0', 'B-b-1'])
  expect(seen).toContainEqual(['A', 'a', 0])
})

test('pipeline drops a throwing item to null and skips its later stages', async () => {
  let stage2Calls = 0
  const r = await runPipeline(
    ['ok', 'bad'],
    async (item: string) => { if (item === 'bad') throw new Error('x'); return item },
    async (prev: string) => { stage2Calls++; return prev + '!' },
  )
  expect(r).toEqual(['ok!', null])
  expect(stage2Calls).toBe(1)
})

test('item cap is an explicit error', async () => {
  const big = Array.from({ length: MAX_ITEMS + 1 }, (_, i) => i)
  await expect(runParallel(big.map(() => async () => 1))).rejects.toThrow(/at most/)
  await expect(runPipeline(big, async (x: number) => x)).rejects.toThrow(/at most/)
})
