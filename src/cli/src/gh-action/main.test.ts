// src/cli/src/gh-action/main.test.ts
import { test, expect } from 'bun:test'
import { runGhActionOnce, realActionDeps } from './main.js'
import { mkdtempSync, mkdirSync, writeFileSync, existsSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

function deps(over: Partial<any> = {}) {
  const calls: string[] = []
  return { calls, d: {
    readEvent: () => ({ repo: 'o/n', issueNumber: 5, isPR: false, task: 'add X', author: 'ulrichando', association: 'OWNER' }),
    allowlist: [] as string[],
    workspace: '/ws',
    neutralizeClaude: (ws: string) => { calls.push(`neutralize:${ws}`); return () => { calls.push('restore') } },
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
  // .claude must be restored AFTER jarvis and BEFORE staging, so the move never lands in the PR
  expect(calls.indexOf('restore')).toBeGreaterThan(calls.indexOf('jarvis'))
  expect(calls.indexOf('restore')).toBeLessThan(calls.findIndex(c => c.startsWith('git:add')))
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

test('realActionDeps.neutralizeClaude moves .claude out of the tree and restores it', () => {
  // A GitHub-workspace-shaped temp repo with a .claude the agent must not see.
  const ws = mkdtempSync(join(tmpdir(), 'gh-ws-'))
  try {
    mkdirSync(join(ws, '.claude'))
    writeFileSync(join(ws, '.claude', 'settings.json'), '{"hooks":"evil"}')
    writeFileSync(join(ws, 'README.md'), 'repo')

    const restore = realActionDeps().neutralizeClaude(ws)
    // Neutralized: .claude is GONE from the tree (so `git add -A` can't stage
    // it and its hooks can't hijack the agent) — the EXDEV bug left it here.
    expect(existsSync(join(ws, '.claude'))).toBe(false)
    expect(existsSync(join(ws, 'README.md'))).toBe(true)

    restore()
    // Restored intact before `git add`.
    expect(existsSync(join(ws, '.claude', 'settings.json'))).toBe(true)
    expect(readFileSync(join(ws, '.claude', 'settings.json'), 'utf8')).toBe('{"hooks":"evil"}')
  } finally {
    rmSync(ws, { recursive: true, force: true })
  }
})

test('neutralizeClaude is a no-op when the repo has no .claude', () => {
  const ws = mkdtempSync(join(tmpdir(), 'gh-ws-'))
  try {
    const restore = realActionDeps().neutralizeClaude(ws)
    expect(typeof restore).toBe('function')
    restore() // must not throw
  } finally {
    rmSync(ws, { recursive: true, force: true })
  }
})
