import { expect, test } from 'bun:test'
import { WorkflowJournal, hashCall } from './journal.js'

test('hashCall is stable across equal (prompt,opts)', () => {
  expect(hashCall('p', { schema: { a: 1 } })).toBe(hashCall('p', { schema: { a: 1 } }))
  expect(hashCall('p', {})).not.toBe(hashCall('q', {}))
})

test('prefix replay: matching prefix returns cached, first mismatch goes live', () => {
  const prior = new WorkflowJournal()
  prior.record(0, 'p1', {}, 'r1')
  prior.record(1, 'p2', {}, 'r2')
  prior.record(2, 'p3', {}, 'r3')

  const resume = WorkflowJournal.fromEntries(prior.entries())
  expect(resume.lookup(0, 'p1', {})).toEqual({ hit: true, result: 'r1' })
  expect(resume.lookup(1, 'CHANGED', {})).toEqual({ hit: false })
  expect(resume.lookup(2, 'p3', {})).toEqual({ hit: false })
})

test('lookup miss when index beyond journal', () => {
  const j = new WorkflowJournal()
  expect(j.lookup(0, 'p', {})).toEqual({ hit: false })
})

test('record is CALL-INDEX addressed, not completion order (parallel resume)', () => {
  // Simulate a parallel workflow: agents are CALLED 0,1,2 but COMPLETE out of
  // order (2, 0, 1). record() must place each result at its call index so a
  // resume replays the right result to the right call — a push()-based journal
  // would store [r2,r0,r1] and mis-map every lookup.
  const j = new WorkflowJournal()
  j.record(2, 'p2', {}, 'r2') // completes first
  j.record(0, 'p0', {}, 'r0')
  j.record(1, 'p1', {}, 'r1')

  const resume = WorkflowJournal.fromEntries(j.entries())
  expect(resume.lookup(0, 'p0', {})).toEqual({ hit: true, result: 'r0' })
  expect(resume.lookup(1, 'p1', {})).toEqual({ hit: true, result: 'r1' })
  expect(resume.lookup(2, 'p2', {})).toEqual({ hit: true, result: 'r2' })
})

test('cache hits are re-recorded so a resumed journal does not degrade', () => {
  // Original run recorded 3 calls.
  const first = new WorkflowJournal()
  first.record(0, 'p0', {}, 'r0')
  first.record(1, 'p1', {}, 'r1')
  first.record(2, 'p2', {}, 'r2')

  // Resume: calls 0,1 hit cache, call 2 diverges and re-runs live.
  const resume = WorkflowJournal.fromEntries(first.entries())
  expect(resume.lookup(0, 'p0', {}).hit).toBe(true)
  expect(resume.lookup(1, 'p1', {}).hit).toBe(true)
  expect(resume.lookup(2, 'CHANGED', {}).hit).toBe(false)
  resume.record(2, 'CHANGED', {}, 'r2-new')

  // The persisted journal must still contain the full prefix (hits included),
  // not just the one live agent — else the next resume loses calls 0 and 1.
  const persisted = resume.entries()
  expect(persisted).toHaveLength(3)
  expect(persisted[0]).toEqual({ hash: hashCall('p0', {}), result: 'r0' })
  expect(persisted[1]).toEqual({ hash: hashCall('p1', {}), result: 'r1' })
  expect(persisted[2]).toEqual({ hash: hashCall('CHANGED', {}), result: 'r2-new' })
})

test('holes (skipped/failed calls) serialize as null and stay index-aligned', () => {
  const j = new WorkflowJournal()
  j.record(0, 'p0', {}, 'r0')
  // index 1 skipped (no record) — a hole
  j.record(2, 'p2', {}, 'r2')

  const entries = j.entries()
  expect(entries).toHaveLength(3)
  expect(entries[1]).toBeNull() // dense null, not a dropped slot

  // Round-trip through the on-disk shape (JSON lines).
  const roundTripped = entries.map(e => JSON.parse(JSON.stringify(e)))
  const resume = WorkflowJournal.fromEntries(roundTripped)
  expect(resume.lookup(0, 'p0', {})).toEqual({ hit: true, result: 'r0' })
  expect(resume.lookup(1, 'p1', {})).toEqual({ hit: false }) // hole → re-run
  expect(resume.lookup(2, 'p2', {})).toEqual({ hit: true, result: 'r2' })
})
