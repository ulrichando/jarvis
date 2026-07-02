// src/cli/src/gh-action/main.test.ts
import { test, expect } from 'bun:test'
import { runGhActionOnce } from './main.js'

function deps(over: Partial<any> = {}) {
  const calls: string[] = []
  return { calls, d: {
    readEvent: () => ({ repo: 'o/n', issueNumber: 5, isPR: false, task: 'add X', author: 'ulrichando', association: 'OWNER' }),
    allowlist: [] as string[],
    workspace: '/ws',
    neutralizeClaude: (ws: string) => { calls.push(`neutralize:${ws}`) },
    exec: async () => { calls.push('jarvis'); return { code: 0, stdout: '', stderr: '' } },
    git: async (a: string[]) => { calls.push(`git:${a.join(' ')}`); return { code: a.includes('--quiet') ? 1 : 0, stdout: '', stderr: '' } },
    gh: async (a: string[]) => { calls.push(`gh:${a[0]} ${a[1] ?? ''}`); return { code: 0, stdout: 'https://pr', stderr: '' } },
    log: () => {}, dryRun: false, ...over,
  } }
}

test('authorized issue → runs jarvis, opens PR', async () => {
  const { calls, d } = deps()
  const r = await runGhActionOnce(d)
  expect(r.ok).toBe(true)
  expect(calls).toContain('jarvis')
  expect(calls.some(c => c.startsWith('gh:pr create'))).toBe(true)
  expect(calls[0]).toBe('neutralize:/ws')          // security step runs BEFORE jarvis
})

test('unauthorized (NONE) → skips, never runs jarvis', async () => {
  const { calls, d } = deps({ readEvent: () => ({ repo: 'o/n', issueNumber: 5, isPR: false, task: 'x', author: 'e', association: 'NONE' }) })
  const r = await runGhActionOnce(d)
  expect(r.skipped).toBe(true)
  expect(calls).not.toContain('jarvis')
})

test('no event (null) → skips cleanly', async () => {
  const { calls, d } = deps({ readEvent: () => null })
  const r = await runGhActionOnce(d)
  expect(r.skipped).toBe(true)
  expect(calls.length).toBe(0)
})

test('fork PR head is refused before jarvis', async () => {
  const { calls, d } = deps({ readEvent: () => ({ repo: 'o/n', issueNumber: 5, isPR: true, task: 'x', author: 'ulrichando', association: 'OWNER' }),
    prIsUntrusted: async () => true })
  const r = await runGhActionOnce(d)
  expect(r.skipped).toBe(true)
  expect(calls).not.toContain('jarvis')
})

test('dry-run posts/pushes nothing', async () => {
  const { calls, d } = deps({ dryRun: true })
  await runGhActionOnce(d)
  expect(calls).not.toContain('jarvis')
  expect(calls.some(c => c.startsWith('gh:'))).toBe(false)
})
