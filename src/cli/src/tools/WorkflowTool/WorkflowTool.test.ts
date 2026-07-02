import { expect, test } from 'bun:test'
import { validateWorkflowScript } from './WorkflowTool.js'

test('accepts a valid deterministic script', () => {
  const r = validateWorkflowScript(
    `export const meta = { name: 'x', description: 'd' }\nconst a = await agent('p')`,
  )
  expect(r.ok).toBe(true)
})

test('rejects a non-deterministic script', () => {
  const r = validateWorkflowScript(
    `export const meta = { name: 'x', description: 'd' }\nconst t = Date.now()`,
  )
  expect(r.ok).toBe(false)
  if (r.ok) return
  expect(r.error).toMatch(/deterministic/i)
})

test('rejects a script with bad meta', () => {
  const r = validateWorkflowScript(`const a = await agent('p')`)
  expect(r.ok).toBe(false)
})
