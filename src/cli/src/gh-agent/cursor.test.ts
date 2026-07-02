// src/cli/src/gh-agent/cursor.test.ts
import { describe, expect, test } from 'bun:test'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { readCursor, advanceCursor } from './cursor.js'

describe('gh-agent cursor', () => {
  test('missing cursor → returns a valid ISO in the past', () => {
    const dir = mkdtempSync(join(tmpdir(), 'ghc-'))
    const iso = readCursor('owner/repo', dir)
    expect(new Date(iso).getTime()).toBeLessThanOrEqual(Date.now())
    rmSync(dir, { recursive: true, force: true })
  })

  test('advance then read returns the advanced value', () => {
    const dir = mkdtempSync(join(tmpdir(), 'ghc-'))
    advanceCursor('owner/repo', '2026-07-02T00:00:00Z', dir)
    expect(readCursor('owner/repo', dir)).toBe('2026-07-02T00:00:00Z')
    rmSync(dir, { recursive: true, force: true })
  })

  test('cursors are per-repo (no cross-contamination)', () => {
    const dir = mkdtempSync(join(tmpdir(), 'ghc-'))
    advanceCursor('owner/a', '2026-01-01T00:00:00Z', dir)
    advanceCursor('owner/b', '2026-02-02T00:00:00Z', dir)
    expect(readCursor('owner/a', dir)).toBe('2026-01-01T00:00:00Z')
    expect(readCursor('owner/b', dir)).toBe('2026-02-02T00:00:00Z')
    rmSync(dir, { recursive: true, force: true })
  })
})
