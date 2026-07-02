import { expect, test } from 'bun:test'
import { runWorkflow } from './runWorkflow.js'

const noBudget = () => ({
  total: null as number | null,
  spent: () => 0,
  remaining: () => Infinity,
})

// Regression guard for the resume seam that the live single-session proof
// exercised (1 agent transcript for 2 runs). Counts dispatches directly:
// a resumed run with the prior journal must return cached results and
// dispatch ZERO agents.
test('resume: priorJournal replays agent results without re-dispatching', async () => {
  let dispatchCalls = 0
  const dispatch = async (p: string) => {
    dispatchCalls++
    return { text: `t:${p}`, tokens: 1, toolCalls: 0 }
  }
  const script = `const a = await agent('one'); const b = await agent('two'); result = { a, b }`

  // First run — dispatches both agents, captures the journal.
  const first = await runWorkflow({
    scriptBody: script,
    args: undefined,
    dispatch,
    getBudget: noBudget,
    resolveWorkflow: async () => null,
    onProgress: () => {},
    signal: new AbortController().signal,
    syncTimeoutMs: 2000,
  })
  expect(first.result).toEqual({ a: 't:one', b: 't:two' })
  expect(dispatchCalls).toBe(2)
  expect(first.journal.length).toBe(2)

  // Resume — same script + priorJournal → every agent() is a cache hit,
  // so dispatch is never called again.
  dispatchCalls = 0
  const resumed = await runWorkflow({
    scriptBody: script,
    args: undefined,
    dispatch,
    getBudget: noBudget,
    resolveWorkflow: async () => null,
    onProgress: () => {},
    signal: new AbortController().signal,
    syncTimeoutMs: 2000,
    priorJournal: first.journal,
  })
  expect(resumed.result).toEqual({ a: 't:one', b: 't:two' })
  expect(dispatchCalls).toBe(0)
})

// A changed agent() call diverges the prefix: the changed call and everything
// after it re-dispatch; the unchanged prefix before it stays cached.
test('resume: divergence re-runs from the first changed call onward', async () => {
  let dispatchCalls = 0
  const dispatch = async (p: string) => {
    dispatchCalls++
    return { text: `t:${p}`, tokens: 1, toolCalls: 0 }
  }
  const first = await runWorkflow({
    scriptBody: `const a = await agent('one'); const b = await agent('two'); result = { a, b }`,
    args: undefined,
    dispatch,
    getBudget: noBudget,
    resolveWorkflow: async () => null,
    onProgress: () => {},
    signal: new AbortController().signal,
    syncTimeoutMs: 2000,
  })
  expect(dispatchCalls).toBe(2)

  // Second agent prompt changed → call 0 cached, call 1 (and onward) re-runs.
  dispatchCalls = 0
  const resumed = await runWorkflow({
    scriptBody: `const a = await agent('one'); const b = await agent('CHANGED'); result = { a, b }`,
    args: undefined,
    dispatch,
    getBudget: noBudget,
    resolveWorkflow: async () => null,
    onProgress: () => {},
    signal: new AbortController().signal,
    syncTimeoutMs: 2000,
    priorJournal: first.journal,
  })
  expect(resumed.result).toEqual({ a: 't:one', b: 't:CHANGED' })
  expect(dispatchCalls).toBe(1) // only the changed call re-dispatched
})
