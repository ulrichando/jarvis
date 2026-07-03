// src/gh-app/sign.test.ts
import { test, expect } from 'bun:test'
import { verifySignature } from './sign.js'
import { createHmac } from 'node:crypto'

function sigFor(b: string, s: string) { return 'sha256=' + createHmac('sha256', s).update(b).digest('hex') }

test('valid X-Hub-Signature-256 passes; tampered fails', () => {
  const secret = 'topsecret', body = '{"a":1}'
  // sha256 hmac known vector computed with the same crypto
  const good = verifySignature(body, sigFor(body, secret), secret)
  expect(good).toBe(true)
  expect(verifySignature(body + 'x', sigFor(body, secret), secret)).toBe(false)
  expect(verifySignature(body, 'sha256=deadbeef', secret)).toBe(false)
})

test('malformed / absent headers and empty secret all fail closed', () => {
  const secret = 'topsecret', body = '{"a":1}'
  expect(verifySignature(body, undefined, secret)).toBe(false)
  expect(verifySignature(body, '', secret)).toBe(false)
  expect(verifySignature(body, 'sha1=' + createHmac('sha1', secret).update(body).digest('hex'), secret)).toBe(false)
  expect(verifySignature(body, 'sha256=not-hex-at-all', secret)).toBe(false)
  expect(verifySignature(body, sigFor(body, secret), '')).toBe(false) // no secret configured → never verify
})

test('operates on raw bytes: Uint8Array body verifies identically', () => {
  const secret = 's3cr3t'
  const bytes = new TextEncoder().encode('{"emoji":"é你"}')
  const sig = 'sha256=' + createHmac('sha256', secret).update(bytes).digest('hex')
  expect(verifySignature(bytes, sig, secret)).toBe(true)
  expect(verifySignature(bytes.slice(1), sig, secret)).toBe(false)
})
