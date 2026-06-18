import { describe, expect, test } from 'bun:test'
import { createHmac } from 'node:crypto'

import {
  PROXY_JWT_AUD,
  peekProxyTokenExp,
  signProxyToken,
  verifyProxyToken,
} from './proxyJwt.js'

const SECRET = 'jarvis-test-secret-deterministic'

// Cross-impl known-answer vector. This exact token is also produced by the web
// mirror (src/web/src/lib/bridge/proxyJwt.ts) for the same inputs
// (sub=user-123, iat=1700000000, ttl=1000s) — verified byte-identical at build
// time. If EITHER impl drifts, this literal stops matching and the suite fails.
const KNOWN =
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.' +
  'eyJzdWIiOiJ1c2VyLTEyMyIsImF1ZCI6ImphcnZpcy1wcm94eSIsImlzcyI6ImphcnZpcy13ZWIiLCJpYXQiOjE3MDAwMDAwMDAsImV4cCI6MTcwMDAwMTAwMH0.' +
  'FvDLfV9PP72lX_GEb8w3TkT7L8WT8US3PAqwKDPDnfg'

/** Forge a CORRECTLY-signed token with arbitrary claims/header — lets us
 * exercise the claim-validation branches (aud/iss/sub) that signProxyToken
 * itself can't produce. */
function signRaw(
  claims: Record<string, unknown>,
  secret = SECRET,
  header: Record<string, unknown> = { alg: 'HS256', typ: 'JWT' },
): string {
  const h = Buffer.from(JSON.stringify(header)).toString('base64url')
  const p = Buffer.from(JSON.stringify(claims)).toString('base64url')
  const sig = createHmac('sha256', secret).update(`${h}.${p}`).digest('base64url')
  return `${h}.${p}.${sig}`
}

describe('proxyJwt — HS256 sign/verify', () => {
  test('sign is deterministic and matches the cross-impl known answer', () => {
    expect(
      signProxyToken({ sub: 'user-123', ttlSeconds: 1000 }, SECRET, 1700000000),
    ).toBe(KNOWN)
  })

  test('round-trips a freshly minted token', () => {
    const tok = signProxyToken({ sub: 'abc', ttlSeconds: 60 }, SECRET, 1000)
    const r = verifyProxyToken(tok, SECRET, 1000)
    expect(r.ok).toBe(true)
    if (r.ok) {
      expect(r.claims.sub).toBe('abc')
      expect(r.claims.aud).toBe(PROXY_JWT_AUD)
    }
  })

  test('verifies the known-answer token within its validity window', () => {
    expect(verifyProxyToken(KNOWN, SECRET, 1700000500).ok).toBe(true)
  })

  test('rejects the wrong secret', () => {
    const r = verifyProxyToken(KNOWN, 'wrong-secret', 1700000500)
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.reason).toBe('signature mismatch')
  })

  test('rejects a tampered payload (forged sub on the original signature)', () => {
    const [h, , s] = KNOWN.split('.')
    const forged = Buffer.from(
      JSON.stringify({ sub: 'attacker', aud: PROXY_JWT_AUD, iss: 'jarvis-web', iat: 1700000000, exp: 1700001000 }),
    ).toString('base64url')
    const r = verifyProxyToken(`${h}.${forged}.${s}`, SECRET, 1700000500)
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.reason).toBe('signature mismatch')
  })

  test('rejects alg=none', () => {
    const r = verifyProxyToken(
      signRaw(
        { sub: 'x', aud: PROXY_JWT_AUD, iss: 'jarvis-web', iat: 1, exp: 9999999999 },
        SECRET,
        { alg: 'none', typ: 'JWT' },
      ),
      SECRET,
      1000,
    )
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.reason).toContain('alg')
  })

  test('rejects an expired token beyond the skew grace', () => {
    const r = verifyProxyToken(KNOWN, SECRET, 1700001000 + 61)
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.reason).toBe('expired')
  })

  test('accepts within the clock-skew grace', () => {
    expect(verifyProxyToken(KNOWN, SECRET, 1700001000 + 30).ok).toBe(true)
  })

  test('rejects the wrong audience even when correctly signed', () => {
    const r = verifyProxyToken(
      signRaw({ sub: 'x', aud: 'someone-else', iss: 'jarvis-web', iat: 1, exp: 9999999999 }),
      SECRET,
      1000,
    )
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.reason).toBe('aud mismatch')
  })

  test('rejects the wrong issuer', () => {
    const r = verifyProxyToken(
      signRaw({ sub: 'x', aud: PROXY_JWT_AUD, iss: 'evil', iat: 1, exp: 9999999999 }),
      SECRET,
      1000,
    )
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.reason).toBe('iss mismatch')
  })

  test('rejects a missing/empty sub', () => {
    const r = verifyProxyToken(
      signRaw({ aud: PROXY_JWT_AUD, iss: 'jarvis-web', iat: 1, exp: 9999999999 }),
      SECRET,
      1000,
    )
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.reason).toBe('missing sub')
  })

  test('rejects malformed input', () => {
    expect(verifyProxyToken('not-a-jwt', SECRET).ok).toBe(false)
    expect(verifyProxyToken('', SECRET).ok).toBe(false)
    expect(verifyProxyToken(KNOWN, '').ok).toBe(false)
  })

  test('peekProxyTokenExp reads exp without verifying', () => {
    expect(peekProxyTokenExp(KNOWN)).toBe(1700001000)
    expect(peekProxyTokenExp('garbage')).toBeNull()
  })
})
