import { expect, test } from 'bun:test'
import { makeAgentFn } from './agentCall.js'
import { WorkflowJournal } from './journal.js'
import { ConcurrencyLimiter } from './limiter.js'

function harness(dispatch: any) {
  const progress: any[] = []
  const journal = new WorkflowJournal()
  const agent = makeAgentFn({
    dispatch,
    journal,
    limiter: new ConcurrencyLimiter(4),
    onProgress: p => progress.push(p),
    getPhase: () => 'Scan',
    nextIndex: (() => { let i = 0; return () => i++ })(),
    signal: new AbortController().signal,
  })
  return { agent, progress, journal }
}

test('returns text result and emits done progress', async () => {
  const { agent, progress } = harness(async () => ({ text: 'hello', tokens: 5, toolCalls: 1 }))
  const r = await agent('do a thing', { label: 'l1' })
  expect(r).toBe('hello')
  expect(progress.some(p => p.state === 'running')).toBe(true)
  expect(progress.some(p => p.state === 'done')).toBe(true)
})

test('schema mode returns the structured object', async () => {
  const { agent } = harness(async () => ({ structured: { ok: true }, tokens: 1, toolCalls: 0 }))
  const r = await agent('x', { schema: { type: 'object' } })
  expect(r).toEqual({ ok: true })
})

test('skip resolves null with skipped-by-user state', async () => {
  const { agent, progress } = harness(async () => ({ skipped: true }))
  const r = await agent('x', {})
  expect(r).toBeNull()
  expect(progress.find(p => p.state === 'error')?.error).toBe('skipped by user')
})

test('terminal failure resolves null with error progress', async () => {
  const { agent, progress } = harness(async () => { throw new Error('api dead') })
  const r = await agent('x', {})
  expect(r).toBeNull()
  expect(progress.find(p => p.state === 'error')?.error).toContain('api dead')
})

test('journal hit short-circuits dispatch', async () => {
  let dispatched = 0
  const { agent, journal } = harness(async () => { dispatched++; return { text: 'live', tokens: 0, toolCalls: 0 } })
  await agent('p', {})
  const resumed = WorkflowJournal.fromEntries(journal.entries())
  const agent2 = makeAgentFn({
    dispatch: async () => { dispatched++; return { text: 'SHOULD-NOT-RUN', tokens: 0, toolCalls: 0 } },
    journal: resumed, limiter: new ConcurrencyLimiter(4),
    onProgress: () => {}, getPhase: () => undefined,
    nextIndex: (() => { let i = 0; return () => i++ })(),
    signal: new AbortController().signal,
  })
  const r = await agent2('p', {})
  expect(r).toBe('live')
  expect(dispatched).toBe(1)
})
