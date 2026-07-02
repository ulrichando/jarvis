import { expect, test } from 'bun:test'
import { isSnipBoundaryMessage, projectSnippedView, projectSnipMessages } from './snipProjection.js'

const boundary = (removed: string[]) => ({
  type: 'system', subtype: 'snip_boundary', uuid: 'b1', isMeta: false,
  content: 'snipped', snipMetadata: { removedUuids: removed, tokensFreed: 100, label: 'x' },
})
const msg = (uuid: string) => ({ type: 'user', uuid, message: { role: 'user', content: 'hi' } })

test('isSnipBoundaryMessage detects the subtype', () => {
  expect(isSnipBoundaryMessage(boundary([]) as any)).toBe(true)
  expect(isSnipBoundaryMessage(msg('u1') as any)).toBe(false)
})

test('projectSnippedView removes messages named in a boundary, keeps the boundary', () => {
  const list = [msg('u1'), msg('u2'), boundary(['u1']), msg('u3')] as any[]
  const out = projectSnippedView(list)
  const uuids = out.map(m => (m as any).uuid)
  expect(uuids).not.toContain('u1')
  expect(uuids).toContain('u2')
  expect(uuids).toContain('u3')
  expect(uuids).toContain('b1')
})

test('projectSnipMessages is the same projection', () => {
  const list = [msg('u1'), boundary(['u1'])] as any[]
  expect(projectSnipMessages(list).map(m => (m as any).uuid)).toEqual(
    projectSnippedView(list).map(m => (m as any).uuid),
  )
})
