// src/cli/src/gh-agent/cursor.test.ts
import { describe, expect, test } from 'bun:test'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { addHandledIds, advanceCursor, readCursor, readHandledIds } from './cursor.js'

describe('gh-agent cursor', () => {
  test('missing cursor → returns a valid ISO in the past', () => {
    const dir = mkdtempSync(join(tmpdir(), 'ghc-'))
    const iso = readCursor('owner/repo', dir)
    expect(new Date(iso).getTime()).toBeLessThanOrEqual(Date.now())
    rmSync(dir, { recursive: true, force: true })
  })

  test('advance then read returns the advanced value (canonical ISO)', () => {
    const dir = mkdtempSync(join(tmpdir(), 'ghc-'))
    advanceCursor('owner/repo', '2026-07-02T00:00:00Z', dir)
    expect(readCursor('owner/repo', dir)).toBe('2026-07-02T00:00:00.000Z')
    rmSync(dir, { recursive: true, force: true })
  })

  test('readCursor canonicalizes a hand-edited but Date-parseable value', () => {
    const dir = mkdtempSync(join(tmpdir(), 'ghc-'))
    writeFileSync(join(dir, 'owner__repo.cursor'), 'July 1 2026')
    const got = readCursor('owner/repo', dir)
    expect(got).toBe(new Date('July 1 2026').toISOString())
    expect(got).toMatch(/\.\d{3}Z$/) // canonical: has millis + Z
    rmSync(dir, { recursive: true, force: true })
  })

  test('cursors are per-repo (no cross-contamination)', () => {
    const dir = mkdtempSync(join(tmpdir(), 'ghc-'))
    advanceCursor('owner/a', '2026-01-01T00:00:00Z', dir)
    advanceCursor('owner/b', '2026-02-02T00:00:00Z', dir)
    expect(readCursor('owner/a', dir)).toBe('2026-01-01T00:00:00.000Z')
    expect(readCursor('owner/b', dir)).toBe('2026-02-02T00:00:00.000Z')
    rmSync(dir, { recursive: true, force: true })
  })

  test('advanceCursor is monotonic — an older iso never regresses the cursor', () => {
    const dir = mkdtempSync(join(tmpdir(), 'ghc-'))
    advanceCursor('owner/repo', '2026-07-02T00:00:00Z', dir)
    // An edited old comment re-entering the sweep must not move the cursor back.
    advanceCursor('owner/repo', '2026-06-01T00:00:00Z', dir)
    expect(readCursor('owner/repo', dir)).toBe('2026-07-02T00:00:00.000Z')
    rmSync(dir, { recursive: true, force: true })
  })

  test('addHandledIds then readHandledIds round-trips the ids as a Set', () => {
    const dir = mkdtempSync(join(tmpdir(), 'ghc-'))
    expect(readHandledIds('owner/repo', dir)).toEqual(new Set())
    addHandledIds('owner/repo', [11, 22], dir)
    addHandledIds('owner/repo', [22, 33], dir)
    expect(readHandledIds('owner/repo', dir)).toEqual(new Set([11, 22, 33]))
    rmSync(dir, { recursive: true, force: true })
  })

  test('handled ids are bounded to the most-recent 500', () => {
    const dir = mkdtempSync(join(tmpdir(), 'ghc-'))
    addHandledIds('owner/repo', Array.from({ length: 600 }, (_, i) => i + 1), dir)
    const ids = readHandledIds('owner/repo', dir)
    expect(ids.size).toBe(500)
    expect(ids.has(600)).toBe(true) // newest kept
    expect(ids.has(101)).toBe(true) // oldest survivor
    expect(ids.has(100)).toBe(false) // older than the window → dropped
    rmSync(dir, { recursive: true, force: true })
  })
})
