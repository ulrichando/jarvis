import { expect, test } from 'bun:test'
import { parseWorkflowMeta, checkDeterminism } from './meta.js'

test('parses a pure literal meta', () => {
  const src = `export const meta = { name: 'find-flaky', description: 'x', phases: [{ title: 'Scan' }] }
phase('Scan')`
  const r = parseWorkflowMeta(src)
  expect('error' in r).toBe(false)
  if ('error' in r) return
  expect(r.meta.name).toBe('find-flaky')
  expect(r.meta.phases?.[0]?.title).toBe('Scan')
  expect(r.scriptBody.startsWith("phase('Scan')")).toBe(true)
})

test('rejects missing meta', () => {
  const r = parseWorkflowMeta(`phase('x')`)
  expect('error' in r).toBe(true)
})

test('rejects computed meta (variable reference)', () => {
  const r = parseWorkflowMeta(`const n = 'x'\nexport const meta = { name: n, description: 'd' }`)
  expect('error' in r).toBe(true)
})

test('rejects meta without required name/description', () => {
  const r = parseWorkflowMeta(`export const meta = { name: 'x' }`)
  expect('error' in r).toBe(true)
})

test('determinism guard flags Date.now / Math.random / new Date()', () => {
  expect(checkDeterminism('const t = Date.now()')).toBe(false)
  expect(checkDeterminism('Math.random()')).toBe(false)
  expect(checkDeterminism('new Date()')).toBe(false)
  expect(checkDeterminism('new Date(args.ts)')).toBe(true)
  expect(checkDeterminism('await agent("x")')).toBe(true)
})
