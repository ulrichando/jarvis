import { expect, test } from 'bun:test'
import { WorkflowJournal, hashCall } from './journal.js'

test('hashCall is stable across equal (prompt,opts)', () => {
  expect(hashCall('p', { schema: { a: 1 } })).toBe(hashCall('p', { schema: { a: 1 } }))
  expect(hashCall('p', {})).not.toBe(hashCall('q', {}))
})

test('prefix replay: matching prefix returns cached, first mismatch goes live', () => {
  const prior = new WorkflowJournal()
  prior.record('p1', {}, 'r1')
  prior.record('p2', {}, 'r2')
  prior.record('p3', {}, 'r3')

  const resume = WorkflowJournal.fromEntries(prior.entries())
  expect(resume.lookup(0, 'p1', {})).toEqual({ hit: true, result: 'r1' })
  expect(resume.lookup(1, 'CHANGED', {})).toEqual({ hit: false })
  expect(resume.lookup(2, 'p3', {})).toEqual({ hit: false })
})

test('lookup miss when index beyond journal', () => {
  const j = new WorkflowJournal()
  expect(j.lookup(0, 'p', {})).toEqual({ hit: false })
})
