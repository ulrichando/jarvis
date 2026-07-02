import { expect, test, beforeEach, afterEach } from 'bun:test'
import { mkdtempSync, writeFileSync, mkdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { loadWorkflowsFromDir, clearNamedWorkflowCache } from './namedWorkflows.js'

let dir: string
beforeEach(() => { dir = mkdtempSync(join(tmpdir(), 'wf-')); clearNamedWorkflowCache() })
afterEach(() => rmSync(dir, { recursive: true, force: true }))

test('loads a valid workflow file and reads meta', async () => {
  mkdirSync(join(dir, 'workflows'), { recursive: true })
  writeFileSync(join(dir, 'workflows', 'spec.mjs'),
    `export const meta = { name: 'spec', description: 'write a spec' }\nphase('go')`)
  const list = await loadWorkflowsFromDir(join(dir, 'workflows'), 'userSettings')
  expect(list).toHaveLength(1)
  expect(list[0]!.name).toBe('spec')
  expect(list[0]!.description).toBe('write a spec')
})

test('skips a file with invalid meta', async () => {
  mkdirSync(join(dir, 'workflows'), { recursive: true })
  writeFileSync(join(dir, 'workflows', 'bad.mjs'), `phase('no meta here')`)
  const list = await loadWorkflowsFromDir(join(dir, 'workflows'), 'userSettings')
  expect(list).toHaveLength(0)
})

test('missing dir returns empty', async () => {
  const list = await loadWorkflowsFromDir(join(dir, 'nope'), 'userSettings')
  expect(list).toEqual([])
})
