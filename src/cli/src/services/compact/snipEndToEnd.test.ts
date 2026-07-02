import { expect, test, beforeEach } from 'bun:test'
import { SnipTool } from '../../tools/SnipTool/SnipTool.js'
import { snipCompactIfNeeded, _resetSnipQueueForTest } from './snipCompact.js'
import { projectSnippedView } from './snipProjection.js'
import { deriveShortMessageId } from '../../utils/messages.js'

// deriveShortMessageId reads only the first 10 hex chars (sans dashes), so
// every uuid below is distinct within that prefix.
const U1 = '11111111-0000-0000-0000-000000000001'
const A1 = '1a1a1a1a-0000-0000-0000-000000000001'
const U2 = '22222222-0000-0000-0000-000000000002'
const A2 = '2a2a2a2a-0000-0000-0000-000000000002'
const U3 = '33333333-0000-0000-0000-000000000003'

const mk = (type: 'user' | 'assistant', uuid: string) =>
  type === 'user'
    ? { type, uuid, message: { role: 'user', content: `text [id:${deriveShortMessageId(uuid)}]` } }
    : { type, uuid, message: { role: 'assistant', content: 'reply reply reply reply' } }

beforeEach(() => _resetSnipQueueForTest())

// Reproduces the exact live turn pipeline end to end:
//   SnipTool.call (enqueue)  →  snipCompactIfNeeded (query boundary drain +
//   boundary insert)  →  projectSnippedView (model-facing projection).
// query.ts:403 calls snipCompactIfNeeded; messages.ts:4653
// (getMessagesAfterCompactBoundary) calls projectSnippedView — so this is the
// real path, not a synthetic one.
test('end-to-end: Snip tool removes the addressed range from the model-facing view', async () => {
  const msgs = [
    mk('user', U1),
    mk('assistant', A1),
    mk('user', U2),
    mk('assistant', A2),
    mk('user', U3),
  ] as never[]

  // 1. The tool enqueues the snip (exactly what happens on a live tool call).
  const toolRes = await (
    SnipTool as unknown as {
      call: (i: unknown, c: unknown) => Promise<{ data: { success: boolean; message: string } }>
    }
  ).call({ start_id: deriveShortMessageId(U1), end_id: deriveShortMessageId(U1) }, {})
  expect(toolRes.data.success).toBe(true)
  expect(toolRes.data.message).toContain('Queued')

  // 2. The query boundary drains the queue and inserts a snip_boundary.
  const drained = snipCompactIfNeeded(msgs)
  expect(drained.executed).toBe(true)
  expect(drained.tokensFreed).toBeGreaterThan(0)
  expect(drained.messages.some((m: { subtype?: string }) => m.subtype === 'snip_boundary')).toBe(true)

  // 3. The model-facing projection drops the snipped range, keeps everything
  //    after it AND the boundary marker.
  const projected = projectSnippedView(drained.messages) as Array<{ uuid?: string; subtype?: string }>
  const uuids = projected.map(m => m.uuid)
  expect(uuids).not.toContain(U1) // first user message — snipped
  expect(uuids).not.toContain(A1) // its assistant reply (same segment) — snipped
  expect(uuids).toContain(U2) // later turn — kept
  expect(uuids).toContain(U3) // current turn — kept
  expect(projected.some(m => m.subtype === 'snip_boundary')).toBe(true) // marker survives projection
})
