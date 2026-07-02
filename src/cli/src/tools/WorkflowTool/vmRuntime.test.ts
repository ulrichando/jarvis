import { expect, test } from 'bun:test'
import { buildWorkflowContext, runScriptBody } from './vmRuntime.js'

function ctx(overrides: any = {}) {
  const logs: string[] = []
  const phases: string[] = []
  return buildWorkflowContext({
    agent: async (p: string) => `ran:${p}`,
    log: (m: string) => logs.push(m),
    phase: (t: string) => phases.push(t),
    getBudget: () => ({ total: null, spent: () => 0, remaining: () => Infinity }),
    args: { q: 'hi' },
    resolveWorkflow: async () => 'nested-result',
    ...overrides,
  })
}

test('script can call agent/parallel/pipeline and set result', async () => {
  const c = ctx()
  const body = `
    phase('Scan')
    const a = await agent('one')
    const b = await parallel([() => agent('p1'), () => agent('p2')])
    const d = await pipeline(['x'], it => agent(it))
    result = { a, b, d }
  `
  const r = await runScriptBody(body, c, { timeout: 2000 })
  expect(r).toEqual({ a: 'ran:one', b: ['ran:p1', 'ran:p2'], d: ['ran:x'] })
})

test('args is exposed verbatim', async () => {
  const c = ctx()
  const r = await runScriptBody(`result = args.q`, c, { timeout: 1000 })
  expect(r).toBe('hi')
})

test('Date.now / Math.random / new Date() throw inside the vm', async () => {
  const c = ctx()
  await expect(runScriptBody(`result = Date.now()`, c, { timeout: 1000 })).rejects.toThrow()
  await expect(runScriptBody(`result = Math.random()`, c, { timeout: 1000 })).rejects.toThrow()
  await expect(runScriptBody(`result = new Date()`, c, { timeout: 1000 })).rejects.toThrow()
})

test('new Date(arg) still works', async () => {
  const c = ctx()
  const r = await runScriptBody(`result = new Date(0).getUTCFullYear()`, c, { timeout: 1000 })
  expect(r).toBe(1970)
})
