// src/cli/src/gh-agent/main.test.ts
import { describe, expect, test } from 'bun:test'
import { mkdtempSync, readdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { runGhAgentOnce } from './main.js'
import { advanceCursor, readCursor, readHandledIds } from './cursor.js'
import { SELF_MARKER, type GhRunner } from './gh.js'
import { DEFAULTS } from './config.js'

// gh api --paginate --slurp shape: one top-level array of PAGES.
const comments = JSON.stringify([[
  { id: 2, body: '@jarvis do X', user: { login: 'ulrichando' }, created_at: '2026-07-01T11:00:00Z', updated_at: '2026-07-01T11:00:00Z', issue_url: 'https://api.github.com/repos/o/r/issues/13', html_url: 'u13' },
  { id: 3, body: '@jarvis do Y', user: { login: 'mallory' }, created_at: '2026-07-01T12:00:00Z', updated_at: '2026-07-01T12:00:00Z', issue_url: 'https://api.github.com/repos/o/r/issues/14', html_url: 'u14' },
]])

function recorder(stdout: string, opts: { postCode?: number; getCode?: number } = {}) {
  const posts: string[][] = []
  const gets: string[][] = []
  const run: GhRunner = async (args) => {
    if (args.includes('POST')) {
      posts.push(args)
      return { stdout: '{}', stderr: '', code: opts.postCode ?? 0 }
    }
    gets.push(args)
    return { stdout, stderr: '', code: opts.getCode ?? 0 }
  }
  return { run, posts, gets }
}

function captureStderr(): { text: () => string; restore: () => void } {
  const orig = process.stderr.write.bind(process.stderr)
  let buf = ''
  process.stderr.write = ((chunk: string | Uint8Array) => {
    buf += typeof chunk === 'string' ? chunk : Buffer.from(chunk).toString()
    return true
  }) as typeof process.stderr.write
  return { text: () => buf, restore: () => { process.stderr.write = orig } }
}

describe('gh-agent runGhAgentOnce', () => {
  test('acks ONLY the allowlisted author (with self-marker); ignored mention is marked handled', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'ghm-'))
    const { run, posts } = recorder(comments)
    await runGhAgentOnce({ repo: 'o/r', dryRun: false }, { run, cfg: { ...DEFAULTS, allowlist: ['ulrichando'] }, cursorDir: dir })
    expect(posts).toHaveLength(1)
    expect(posts[0].join(' ')).toContain('repos/o/r/issues/13/comments')
    // Every ack carries the self-marker so the next sweep can filter it out.
    expect(posts[0].some(a => a.includes(SELF_MARKER))).toBe(true)
    // Both the acked AND the ignored (non-allowlisted) mention are handled now.
    const handled = readHandledIds('o/r', dir)
    expect(handled.has(2)).toBe(true)
    expect(handled.has(3)).toBe(true)
    rmSync(dir, { recursive: true, force: true })
  })

  test('no-replay: a second sweep over the same comments posts zero acks (id dedupe)', async () => {
    // GitHub's ?since= is INCLUSIVE (updated_at >= since), so a real second
    // sweep re-fetches the mention it just handled. The stub models that by
    // returning the same comments regardless of `since`. Without comment-id
    // dedupe the agent posts a duplicate acknowledgement.
    const dir = mkdtempSync(join(tmpdir(), 'ghm-'))
    const { run, posts } = recorder(comments)
    const deps = { run, cfg: { ...DEFAULTS, allowlist: ['ulrichando'] }, cursorDir: dir }
    await runGhAgentOnce({ repo: 'o/r', dryRun: false }, deps)
    expect(posts).toHaveLength(1)
    await runGhAgentOnce({ repo: 'o/r', dryRun: false }, deps)
    expect(posts).toHaveLength(1)
    rmSync(dir, { recursive: true, force: true })
  })

  test('dry-run posts nothing AND persists nothing; the real run still consumes the mention', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'ghm-'))
    const { run, posts } = recorder(comments)
    const deps = { run, cfg: { ...DEFAULTS, allowlist: ['ulrichando'] }, cursorDir: dir }
    await runGhAgentOnce({ repo: 'o/r', dryRun: true }, deps)
    expect(posts).toHaveLength(0)
    // NOTHING written: no cursor, no handled ids — a preview must not consume.
    expect(readdirSync(dir)).toEqual([])
    // The real run right after still sees + acks the mention.
    await runGhAgentOnce({ repo: 'o/r', dryRun: false }, deps)
    expect(posts).toHaveLength(1)
    rmSync(dir, { recursive: true, force: true })
  })

  test('failed ack is NOT marked handled (retried next sweep) and sets exitCode', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'ghm-'))
    const prevExit = process.exitCode
    try {
      const bad = recorder(comments, { postCode: 1 })
      const cfg = { ...DEFAULTS, allowlist: ['ulrichando'] }
      await runGhAgentOnce({ repo: 'o/r', dryRun: false }, { run: bad.run, cfg, cursorDir: dir })
      expect(bad.posts).toHaveLength(1) // attempted…
      expect(process.exitCode).toBe(1) // …failed loudly…
      const handled = readHandledIds('o/r', dir)
      expect(handled.has(2)).toBe(false) // …and NOT consumed.
      expect(handled.has(3)).toBe(true) // ignored mention is still handled.
      // Next sweep (POST healthy again) retries and lands the ack.
      const good = recorder(comments)
      await runGhAgentOnce({ repo: 'o/r', dryRun: false }, { run: good.run, cfg, cursorDir: dir })
      expect(good.posts).toHaveLength(1)
      expect(readHandledIds('o/r', dir).has(2)).toBe(true)
    } finally {
      process.exitCode = prevExit
      rmSync(dir, { recursive: true, force: true })
    }
  })

  test('failed ack stays inside the since-window (retry works against REAL ?since= semantics)', async () => {
    // The always-return stub above hides a trap: if the sweep advanced the
    // cursor to maxUpdatedAt (12:00) past the FAILED mention (11:00), a real
    // GitHub fetch (updated_at >= since) would never return it again — the
    // "retried next sweep" guarantee would be silently broken. Model the real
    // inclusive since filter here.
    const dir = mkdtempSync(join(tmpdir(), 'ghm-'))
    const prevExit = process.exitCode
    try {
      const all = [
        { id: 2, body: '@jarvis do X', user: { login: 'ulrichando' }, created_at: '2026-07-01T11:00:00Z', updated_at: '2026-07-01T11:00:00Z', issue_url: 'https://api.github.com/repos/o/r/issues/13', html_url: 'u13' },
        { id: 3, body: 'unrelated chatter', user: { login: 'bob' }, created_at: '2026-07-01T12:00:00Z', updated_at: '2026-07-01T12:00:00Z', issue_url: 'https://api.github.com/repos/o/r/issues/14', html_url: 'u14' },
      ]
      const sinceAware = (postCode: number) => {
        const posts: string[][] = []
        const run: GhRunner = async (args) => {
          if (args.includes('POST')) {
            posts.push(args)
            return { stdout: '{}', stderr: '', code: postCode }
          }
          const since = decodeURIComponent(args[1].match(/since=([^&]+)/)![1])
          const visible = all.filter(c => new Date(c.updated_at).getTime() >= new Date(since).getTime())
          return { stdout: JSON.stringify([visible]), stderr: '', code: 0 }
        }
        return { run, posts }
      }
      const cfg = { ...DEFAULTS, allowlist: ['ulrichando'] }
      // Seed the window before the fixtures (default first-run cursor is
      // now-1h, which would filter these 2026-07-01 comments out entirely).
      advanceCursor('o/r', '2026-07-01T10:00:00Z', dir)
      const bad = sinceAware(1)
      await runGhAgentOnce({ repo: 'o/r', dryRun: false }, { run: bad.run, cfg, cursorDir: dir })
      expect(bad.posts).toHaveLength(1) // attempted, failed
      // Next sweep, POST healthy: the failed mention MUST still be in-window.
      const good = sinceAware(0)
      await runGhAgentOnce({ repo: 'o/r', dryRun: false }, { run: good.run, cfg, cursorDir: dir })
      expect(good.posts).toHaveLength(1) // retried + landed
      expect(readHandledIds('o/r', dir).has(2)).toBe(true)
    } finally {
      process.exitCode = prevExit
      rmSync(dir, { recursive: true, force: true })
    }
  })

  test('a bot ack carrying the self-marker is never re-acked (no self-loop)', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'ghm-'))
    const ack = JSON.stringify([[
      { id: 50, body: `👀 Jarvis received this from @ulrichando: "@jarvis do X"\n\n${SELF_MARKER}`, user: { login: 'ulrichando' }, created_at: '2026-07-01T11:05:00Z', updated_at: '2026-07-01T11:05:00Z', issue_url: 'https://api.github.com/repos/o/r/issues/13', html_url: 'u13b' },
    ]])
    const { run, posts } = recorder(ack)
    await runGhAgentOnce({ repo: 'o/r', dryRun: false }, { run, cfg: { ...DEFAULTS, allowlist: ['ulrichando'] }, cursorDir: dir })
    expect(posts).toHaveLength(0)
    rmSync(dir, { recursive: true, force: true })
  })

  test('window advances past unrelated comments even when nothing is actionable', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'ghm-'))
    const chatter = JSON.stringify([[
      { id: 60, body: 'just chatter, no trigger', user: { login: 'bob' }, created_at: '2026-07-01T12:00:00Z', updated_at: '2026-07-01T12:00:00Z', issue_url: 'https://api.github.com/repos/o/r/issues/9', html_url: 'u9' },
    ]])
    const { run, posts } = recorder(chatter)
    await runGhAgentOnce({ repo: 'o/r', dryRun: false }, { run, cfg: { ...DEFAULTS }, cursorDir: dir })
    expect(posts).toHaveLength(0)
    // readCursor canonicalizes, so the stored window floor reads back with millis.
    expect(readCursor('o/r', dir)).toBe('2026-07-01T12:00:00.000Z')
    rmSync(dir, { recursive: true, force: true })
  })

  test('poll failure (gh error) → stderr warning + exitCode 1 + no state writes', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'ghm-'))
    const prevExit = process.exitCode
    const err = captureStderr()
    try {
      const { run, posts } = recorder('', { getCode: 1 })
      await runGhAgentOnce({ repo: 'o/r', dryRun: false }, { run, cfg: { ...DEFAULTS }, cursorDir: dir })
      expect(posts).toHaveLength(0)
      expect(err.text()).toContain('poll failed')
      expect(process.exitCode).toBe(1)
      expect(readdirSync(dir)).toEqual([]) // a failed fetch must not move the window
    } finally {
      err.restore()
      process.exitCode = prevExit
      rmSync(dir, { recursive: true, force: true })
    }
  })

  test('malformed repo is skipped with a warning — no gh call ever runs for it', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'ghm-'))
    const err = captureStderr()
    try {
      const { run, posts, gets } = recorder(comments)
      await runGhAgentOnce({ repo: 'o/r; rm -rf /', dryRun: true }, { run, cfg: { ...DEFAULTS }, cursorDir: dir })
      expect(gets).toHaveLength(0)
      expect(posts).toHaveLength(0)
      expect(err.text()).toContain('malformed repo')
    } finally {
      err.restore()
      rmSync(dir, { recursive: true, force: true })
    }
  })
})
