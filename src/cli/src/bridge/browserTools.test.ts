// src/cli/src/bridge/browserTools.test.ts
import { test, expect } from 'bun:test'
import { TOOLS, SYSTEM } from './browserTools.js'

test('TOOLS has the 22 proven browser actions with unique names + valid schemas', () => {
  expect(TOOLS.length).toBe(22)
  const names = TOOLS.map(t => t.name)
  expect(new Set(names).size).toBe(22) // unique
  expect(names).toContain('navigate')
  expect(names).toContain('list_tabs')
  expect(names).not.toContain('title') // folded into get_url
  for (const t of TOOLS) {
    expect(typeof t.name).toBe('string')
    expect(typeof t.description).toBe('string')
    expect(t.input_schema.type).toBe('object')
  }
})

test('SYSTEM prompt mentions the tab-workflow guidance', () => {
  expect(SYSTEM).toContain('list_tabs')
})
