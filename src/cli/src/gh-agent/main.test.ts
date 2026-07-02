// src/cli/src/gh-agent/main.test.ts
import { describe, expect, test } from 'bun:test'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { runGhAgentOnce } from './main.js'
import type { GhRunner } from './gh.js'
import { DEFAULTS } from './config.js'

const comments = JSON.stringify([
  { id: 2, body: '@jarvis do X', user: { login: 'ulrichando' }, created_at: '2026-07-01T11:00:00Z', issue_url: 'https://api.github.com/repos/o/r/issues/13', html_url: 'u13' },
  { id: 3, body: '@jarvis do Y', user: { login: 'mallory' }, created_at: '2026-07-01T12:00:00Z', issue_url: 'https://api.github.com/repos/o/r/issues/14', html_url: 'u14' },
])

function recorder(stdout: string) {
  const posts: string[][] = []
  const run: GhRunner = async (args) => {
    if (args[1] === '-X' || args.includes('POST')) { posts.push(args); return { stdout: '{}', stderr: '', code: 0 } }
    return { stdout, stderr: '', code: 0 }
  }
  return { run, posts }
}

describe('gh-agent runGhAgentOnce', () => {
  test('posts an ack ONLY for the allowlisted author, skips others', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'ghm-'))
    const { run, posts } = recorder(comments)
    await runGhAgentOnce({ repo: 'o/r', dryRun: false }, { run, cfg: { ...DEFAULTS, allowlist: ['ulrichando'] }, cursorDir: dir })
    expect(posts).toHaveLength(1)
    expect(posts[0].join(' ')).toContain('repos/o/r/issues/13/comments')
    rmSync(dir, { recursive: true, force: true })
  })

  test('dry-run posts nothing', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'ghm-'))
    const { run, posts } = recorder(comments)
    await runGhAgentOnce({ repo: 'o/r', dryRun: true }, { run, cfg: { ...DEFAULTS, allowlist: ['ulrichando'] }, cursorDir: dir })
    expect(posts).toHaveLength(0)
    rmSync(dir, { recursive: true, force: true })
  })
})
