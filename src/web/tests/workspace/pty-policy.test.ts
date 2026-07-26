import { describe, expect, test } from 'vitest'
// Pure predicates, imported without node-pty so this runs in CI (the #161 pty
// test couldn't load because it pulled in the native module).
import {
  computeRequireAuth,
  resolveShellMode,
} from '../../scripts/lib/pty-policy.mjs'

describe('computeRequireAuth (finding #4 — PTY auth ON by default)', () => {
  test('default (unset) requires auth, on loopback and off', () => {
    expect(computeRequireAuth({}, '127.0.0.1')).toBe(true)
    expect(computeRequireAuth({}, '0.0.0.0')).toBe(true)
  })

  test('explicit =0 disables auth ONLY on loopback', () => {
    expect(
      computeRequireAuth({ JARVIS_PTY_REQUIRE_AUTH: '0' }, '127.0.0.1'),
    ).toBe(false)
    expect(
      computeRequireAuth({ JARVIS_PTY_REQUIRE_AUTH: '0' }, '0.0.0.0'),
    ).toBe(true)
  })

  test('any non-"0" value (incl. "1"/garbage/empty) fails secure to ON', () => {
    for (const v of ['1', 'yes', 'false', '', 'true']) {
      expect(
        computeRequireAuth({ JARVIS_PTY_REQUIRE_AUTH: v }, '127.0.0.1'),
      ).toBe(true)
    }
  })
})

describe('resolveShellMode (finding #4 — host-shell gated, never a silent fallback)', () => {
  test('docker mode always resolves to docker', () => {
    expect(
      resolveShellMode({ mode: 'docker', allowHostShell: false, requireLocalAuth: false }),
    ).toEqual({ mode: 'docker' })
    expect(
      resolveShellMode({ mode: 'docker', allowHostShell: true, requireLocalAuth: true }),
    ).toEqual({ mode: 'docker' })
  })

  test('local REFUSES (error) unless host-shell is explicitly allowed', () => {
    const denied = resolveShellMode({
      mode: 'local',
      allowHostShell: false,
      requireLocalAuth: false,
    })
    expect(denied.mode).toBe('docker')
    expect(denied.error).toMatch(/host shell disabled/)
  })

  test('local allowed only with opt-in AND not under require-local-auth', () => {
    expect(
      resolveShellMode({ mode: 'local', allowHostShell: true, requireLocalAuth: false }),
    ).toEqual({ mode: 'local' })
    const forced = resolveShellMode({
      mode: 'local',
      allowHostShell: true,
      requireLocalAuth: true,
    })
    expect(forced.mode).toBe('docker')
    expect(forced.error).toMatch(/JARVIS_REQUIRE_LOCAL_AUTH/)
  })
})
