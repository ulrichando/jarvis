import { expect, test, beforeEach } from 'bun:test'
import {
  isSnipRuntimeEnabled, shouldNudgeForSnips, snipCompactIfNeeded,
  SNIP_NUDGE_TEXT, _queueSnip, _resetSnipQueueForTest,
} from './snipCompact.js'
import { deriveShortMessageId } from '../../utils/messages.js'

// Distinct within the first 10 hex chars (deriveShortMessageId reads those).
const U = (n: number) => `${String(n).repeat(8)}-0000-0000-0000-00000000000${n}`
const user = (n: number) => ({ type: 'user', uuid: U(n), message: { role: 'user', content: `hi [id:${deriveShortMessageId(U(n))}]` } })
const asst = (n: number) => ({ type: 'assistant', uuid: U(n), message: { role: 'assistant', content: 'x'.repeat(200) } })

beforeEach(() => _resetSnipQueueForTest())

test('runtime enabled unless JARVIS_HISTORY_SNIP=0', () => {
  delete process.env.JARVIS_HISTORY_SNIP
  expect(isSnipRuntimeEnabled()).toBe(true)
  process.env.JARVIS_HISTORY_SNIP = '0'
  expect(isSnipRuntimeEnabled()).toBe(false)
  delete process.env.JARVIS_HISTORY_SNIP
})

test('SNIP_NUDGE_TEXT is a non-empty string', () => {
  expect(typeof SNIP_NUDGE_TEXT).toBe('string')
  expect(SNIP_NUDGE_TEXT.length).toBeGreaterThan(10)
})

test('no queued snip → no-op pass', () => {
  const msgs = [user(1), asst(1), user(2)] as any[]
  const r = snipCompactIfNeeded(msgs)
  expect(r.executed).toBe(false)
  expect(r.messages).toBe(msgs)
})

test('queued snip → inserts a boundary and reports tokensFreed', () => {
  const msgs = [user(1), asst(1), user(2), asst(2), user(3)] as any[]
  _queueSnip(deriveShortMessageId(U(1)), deriveShortMessageId(U(2)))
  const r = snipCompactIfNeeded(msgs)
  expect(r.executed).toBe(true)
  expect(r.tokensFreed).toBeGreaterThan(0)
  expect(r.boundaryMessage).toBeDefined()
  expect(r.messages.some((m: any) => m.subtype === 'snip_boundary')).toBe(true)
})

test('invalid queued range → no-op (no boundary)', () => {
  const msgs = [user(1)] as any[]
  _queueSnip('zzzzzz', 'zzzzzz')
  const r = snipCompactIfNeeded(msgs)
  expect(r.executed).toBe(false)
  expect(r.boundaryMessage).toBeUndefined()
})
