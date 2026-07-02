import { expect, test } from 'bun:test'
import { resolveSnipRange, buildSnipBoundary } from './snipRange.js'
import { deriveShortMessageId } from '../../utils/messages.js'

function user(uuid: string) { return { type: 'user', uuid, message: { role: 'user', content: `hi [id:${deriveShortMessageId(uuid)}]` } } }
function asst(uuid: string) { return { type: 'assistant', uuid, message: { role: 'assistant', content: 'ok' } } }

// UUIDs that differ in the first 10 hex chars so deriveShortMessageId returns
// distinct anchors for each (the formula reads the first 10 hex digits only).
const U = (n: number) =>
  `${n}${n}${n}${n}${n}${n}${n}${n}-0000-0000-0000-00000000000${n}`

test('resolves a mid-transcript range by anchor, excludes the latest user turn', () => {
  const msgs = [user(U(1)), asst(U(1)), user(U(2)), asst(U(2)), user(U(3))] as any[]
  const r = resolveSnipRange(msgs, deriveShortMessageId(U(1)), deriveShortMessageId(U(2)))
  expect('error' in r).toBe(false)
  if ('error' in r) return
  expect(r.removedUuids).toContain(U(1))
  expect(r.removedUuids).toContain(U(2))
  expect(r.removedUuids).not.toContain(U(3))
})

test('rejects a range that includes the latest non-meta user message', () => {
  const msgs = [user(U(1)), asst(U(1)), user(U(3))] as any[]
  const r = resolveSnipRange(msgs, deriveShortMessageId(U(1)), deriveShortMessageId(U(3)))
  expect('error' in r).toBe(true)
})

test('rejects an unresolvable anchor', () => {
  const msgs = [user(U(1))] as any[]
  const r = resolveSnipRange(msgs, 'zzzzzz', 'zzzzzz')
  expect('error' in r).toBe(true)
})

test('buildSnipBoundary carries removedUuids + tokensFreed in the resume shape', () => {
  const b = buildSnipBoundary([U(1), U(2)], 250) as any
  expect(b.type).toBe('system')
  expect(b.subtype).toBe('snip_boundary')
  expect(b.snipMetadata.removedUuids).toEqual([U(1), U(2)])
  expect(b.snipMetadata.tokensFreed).toBe(250)
})
