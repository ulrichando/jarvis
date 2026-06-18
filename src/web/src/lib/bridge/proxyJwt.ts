/**
 * Minimal HS256 JWT for the JARVIS local-proxy credential ("OAuth via login").
 *
 * This is the web (authorization-server) side: it signs the token that the
 * local CLI proxy verifies offline. MIRROR of
 * src/cli/src/proxy/proxyJwt.ts — the two MUST stay byte-compatible. The
 * cross-impl known-answer vector in both *.test.ts files fails loudly on drift.
 *
 * NOTE: deliberately NOT `server-only` — it is pure (no fs / no secrets of its
 * own) and is imported by the vitest suite, which runs in a node (non-RSC)
 * environment where a `server-only` import would throw.
 */
import { createHmac, timingSafeEqual } from "node:crypto";

export const PROXY_JWT_ALG = "HS256";
export const PROXY_JWT_AUD = "jarvis-proxy";
export const PROXY_JWT_ISS = "jarvis-web";
/** Accept tokens up to this many seconds past exp, to tolerate clock skew. */
const CLOCK_SKEW_S = 60;

export type ProxyTokenClaims = {
  /** JARVIS user id the token was minted for. */
  sub: string;
  aud: string;
  iss: string;
  iat: number;
  exp: number;
  jti?: string;
};

function b64urlJson(obj: unknown): string {
  return Buffer.from(JSON.stringify(obj)).toString("base64url");
}

function hmac(data: string, secret: string): Buffer {
  return createHmac("sha256", secret).update(data).digest();
}

export function signProxyToken(
  claims: { sub: string; ttlSeconds: number; jti?: string },
  secret: string,
  nowS: number = Math.floor(Date.now() / 1000),
): string {
  const header = { alg: PROXY_JWT_ALG, typ: "JWT" };
  const payload: ProxyTokenClaims = {
    sub: claims.sub,
    aud: PROXY_JWT_AUD,
    iss: PROXY_JWT_ISS,
    iat: nowS,
    exp: nowS + claims.ttlSeconds,
    ...(claims.jti ? { jti: claims.jti } : {}),
  };
  const signingInput = `${b64urlJson(header)}.${b64urlJson(payload)}`;
  const sig = hmac(signingInput, secret).toString("base64url");
  return `${signingInput}.${sig}`;
}

export type VerifyResult =
  | { ok: true; claims: ProxyTokenClaims }
  | { ok: false; reason: string };

export function verifyProxyToken(
  token: string,
  secret: string,
  nowS: number = Math.floor(Date.now() / 1000),
): VerifyResult {
  if (!token || !secret) return { ok: false, reason: "missing token or secret" };
  const parts = token.split(".");
  if (parts.length !== 3) return { ok: false, reason: "malformed" };
  const [h, p, s] = parts as [string, string, string];

  let header: { alg?: unknown; typ?: unknown };
  try {
    header = JSON.parse(Buffer.from(h, "base64url").toString("utf8"));
  } catch {
    return { ok: false, reason: "bad header" };
  }
  if (header.alg !== PROXY_JWT_ALG) {
    return { ok: false, reason: `unexpected alg ${String(header.alg)}` };
  }

  const expected = hmac(`${h}.${p}`, secret);
  let given: Buffer;
  try {
    given = Buffer.from(s, "base64url");
  } catch {
    return { ok: false, reason: "bad signature encoding" };
  }
  if (given.length !== expected.length || !timingSafeEqual(given, expected)) {
    return { ok: false, reason: "signature mismatch" };
  }

  let claims: ProxyTokenClaims;
  try {
    claims = JSON.parse(Buffer.from(p, "base64url").toString("utf8"));
  } catch {
    return { ok: false, reason: "bad payload" };
  }
  if (claims.aud !== PROXY_JWT_AUD) return { ok: false, reason: "aud mismatch" };
  if (claims.iss !== PROXY_JWT_ISS) return { ok: false, reason: "iss mismatch" };
  if (typeof claims.sub !== "string" || claims.sub.length === 0) {
    return { ok: false, reason: "missing sub" };
  }
  if (typeof claims.exp !== "number" || nowS > claims.exp + CLOCK_SKEW_S) {
    return { ok: false, reason: "expired" };
  }
  return { ok: true, claims };
}
