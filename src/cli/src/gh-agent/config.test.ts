// src/cli/src/gh-agent/config.test.ts
import { describe, expect, test } from 'bun:test'
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { loadGhAgentConfig, isAllowedAuthor, DEFAULTS } from './config.js'

describe('gh-agent config', () => {
  test('missing file → defaults (allowlist = ulrichando)', () => {
    const cfg = loadGhAgentConfig(join(tmpdir(), 'nope-gh-agent.json'))
    expect(cfg.allowlist).toEqual(['ulrichando'])
    expect(cfg.trigger).toBe('@jarvis')
    expect(cfg.pollSeconds).toBe(45)
    expect(cfg.repos).toEqual([])
  })

  test('file overrides merge over defaults', () => {
    const dir = mkdtempSync(join(tmpdir(), 'gha-'))
    const p = join(dir, 'gh-agent.json')
    writeFileSync(p, JSON.stringify({ repos: ['o/r'], allowlist: ['alice'], pollSeconds: 10 }))
    const cfg = loadGhAgentConfig(p)
    expect(cfg.repos).toEqual(['o/r'])
    expect(cfg.allowlist).toEqual(['alice'])
    expect(cfg.pollSeconds).toBe(10)
    expect(cfg.trigger).toBe('@jarvis') // still defaulted
    rmSync(dir, { recursive: true, force: true })
  })

  test('non-string entries in repos/allowlist are filtered out (never throws)', () => {
    const dir = mkdtempSync(join(tmpdir(), 'gha-'))
    const p = join(dir, 'gh-agent.json')
    writeFileSync(p, JSON.stringify({ repos: ['o/r', 7, null], allowlist: [42, 'alice'] }))
    const cfg = loadGhAgentConfig(p)
    expect(cfg.repos).toEqual(['o/r'])
    expect(cfg.allowlist).toEqual(['alice'])
    rmSync(dir, { recursive: true, force: true })
  })

  test('malformed JSON → defaults (never throws)', () => {
    const dir = mkdtempSync(join(tmpdir(), 'gha-'))
    const p = join(dir, 'gh-agent.json')
    writeFileSync(p, '{ not json')
    expect(loadGhAgentConfig(p)).toEqual(DEFAULTS)
    rmSync(dir, { recursive: true, force: true })
  })

  test('isAllowedAuthor is case-insensitive and exact', () => {
    const cfg = { ...DEFAULTS, allowlist: ['Alice'] }
    expect(isAllowedAuthor(cfg, 'alice')).toBe(true)
    expect(isAllowedAuthor(cfg, 'ALICE')).toBe(true)
    expect(isAllowedAuthor(cfg, 'mallory')).toBe(false)
  })
})
