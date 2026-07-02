import { expect, test } from 'bun:test'
import { runWorkflow } from './runWorkflow.js'

const noBudget = () => ({ total: null as number | null, spent: () => 0, remaining: () => Infinity })

test('runs a script to completion and serializes result', async () => {
  const out = await runWorkflow({
    scriptBody: `const a = await agent('x'); result = { a }`,
    args: undefined,
    dispatch: async (p: string) => ({ text: `t:${p}`, tokens: 3, toolCalls: 1 }),
    getBudget: noBudget,
    resolveWorkflow: async () => null,
    onProgress: () => {},
    signal: new AbortController().signal,
    syncTimeoutMs: 2000,
  })
  expect(out.error).toBeUndefined()
  expect(out.result).toEqual({ a: 't:x' })
  expect(out.agentCount).toBe(1)
})

test('captures logs and failures', async () => {
  const out = await runWorkflow({
    scriptBody: `log('hi'); const a = await agent('boom'); result = a`,
    args: undefined,
    dispatch: async () => { throw new Error('dead') },
    getBudget: noBudget, resolveWorkflow: async () => null,
    onProgress: () => {}, signal: new AbortController().signal, syncTimeoutMs: 2000,
  })
  expect(out.logs).toContain('hi')
  expect(out.result).toBeNull()
})

test('script error is captured, not thrown', async () => {
  const out = await runWorkflow({
    scriptBody: `throw new Error('script boom')`,
    args: undefined, dispatch: async () => ({ text: '', tokens: 0, toolCalls: 0 }),
    getBudget: noBudget, resolveWorkflow: async () => null,
    onProgress: () => {}, signal: new AbortController().signal, syncTimeoutMs: 2000,
  })
  expect(out.error).toContain('script boom')
})

test('abort rejects the run with a killed error', async () => {
  const ac = new AbortController()
  const p = runWorkflow({
    scriptBody: `await new Promise(r => setTimeout(r, 5000)); result = 1`,
    args: undefined, dispatch: async () => ({ text: '', tokens: 0, toolCalls: 0 }),
    getBudget: noBudget, resolveWorkflow: async () => null,
    onProgress: () => {}, signal: ac.signal, syncTimeoutMs: 10000,
  })
  ac.abort()
  const out = await p
  expect(out.error).toMatch(/abort/i)
})
