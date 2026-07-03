// src/gh-app/sign.ts — GitHub webhook HMAC verification.
//
// GitHub signs every delivery with HMAC-SHA256(rawBody, webhookSecret) and
// sends it as `X-Hub-Signature-256: sha256=<hex>`. This is the ONLY auth on
// POST /webhook (the hostname is excluded from Cloudflare Access so GitHub
// can reach it), so it must be strict: constant-time compare
// (timingSafeEqual, mirroring proxyJwt.ts), raw body bytes (never a
// re-serialized JSON parse), and fail-closed on anything malformed.
import { createHmac, timingSafeEqual } from 'node:crypto'

const PREFIX = 'sha256='
const HEX_RE = /^[0-9a-f]+$/i

export function verifySignature(
  rawBody: string | Uint8Array,
  signatureHeader: string | null | undefined,
  secret: string,
): boolean {
  // No secret configured = the service is not set up; never accept.
  if (!secret) return false
  if (typeof signatureHeader !== 'string' || !signatureHeader.startsWith(PREFIX)) return false
  const hex = signatureHeader.slice(PREFIX.length)
  // Strict hex gate: Buffer.from(_, 'hex') silently truncates at the first
  // bad char, which would otherwise fold garbage into a length mismatch.
  if (hex.length !== 64 || !HEX_RE.test(hex)) return false
  const given = Buffer.from(hex, 'hex')
  const expected = createHmac('sha256', secret).update(rawBody).digest()
  // Length is fixed (32) by the gates above; timingSafeEqual throws on
  // mismatch, so keep the guard anyway (fail closed, never throw).
  if (given.length !== expected.length) return false
  return timingSafeEqual(given, expected)
}
