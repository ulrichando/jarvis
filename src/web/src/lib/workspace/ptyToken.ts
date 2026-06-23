/**
 * Minimal HS256 JWT scoping a single /code terminal (PTY) websocket to one
 * workspace + one logged-in user. The browser fetches one of these from the
 * authed mint route (/api/workspace/[id]/pty-token) right before it opens the
 * websocket, and the PTY sidecar (scripts/pty-server.mjs) verifies it OFFLINE
 * before spawning a shell. The web app is never on the websocket path.
 *
 * Shape mirrors src/lib/bridge/proxyJwt.ts on purpose (same HS256 over
 * node:crypto, no external dep). The verify half is duplicated in plain Node at
 * scripts/lib/pty-auth.mjs because the sidecar runs outside the Next bundle and
 * cannot import this TS module — exactly the runtime boundary that
 * src/lib/workspace/docker.ts ↔ scripts/lib/docker.mjs already straddles. The
 * cross-impl known-answer vector in tests/workspace/pty-token.test.ts fails
 * loudly if the two halves drift.
 *
 * The signing secret is reused from JARVIS_PROXY_JWT_SECRET (one secret to
 * manage); a distinct `aud` ("jarvis-pty" vs "jarvis-proxy") makes the two
 * token families non-interchangeable — a proxy token can't open a shell and a
 * pty token can't talk to the proxy.
 *
 * Deliberately NOT `server-only`: it is pure (no fs, no ambient secret) and the
 * vitest suite imports it in a plain-node env where `server-only` would throw.
 */
import { createHmac, timingSafeEqual } from "node:crypto";

export const PTY_JWT_ALG = "HS256";
export const PTY_JWT_AUD = "jarvis-pty";
export const PTY_JWT_ISS = "jarvis-web";
/** Accept tokens up to this many seconds past exp, to tolerate clock skew. */
const CLOCK_SKEW_S = 60;

export type PtyTokenClaims = {
  /** JARVIS user id the token was minted for (audit/traceability). */
  sub: string;
  /** Workspace id this terminal is scoped to. */
  wsid: string;
  aud: string;
  iss: string;
  iat: number;
  exp: number;
};

function b64urlJson(obj: unknown): string {
  return Buffer.from(JSON.stringify(obj)).toString("base64url");
}

function hmac(data: string, secret: string): Buffer {
  return createHmac("sha256", secret).update(data).digest();
}

export function signPtyToken(
  claims: { sub: string; wsid: string; ttlSeconds: number },
  secret: string,
  nowS: number = Math.floor(Date.now() / 1000),
): string {
  const header = { alg: PTY_JWT_ALG, typ: "JWT" };
  // Field order is part of the wire format — keep in lockstep with the .mjs
  // twin and the known-answer vector.
  const payload: PtyTokenClaims = {
    sub: claims.sub,
    wsid: claims.wsid,
    aud: PTY_JWT_AUD,
    iss: PTY_JWT_ISS,
    iat: nowS,
    exp: nowS + claims.ttlSeconds,
  };
  const signingInput = `${b64urlJson(header)}.${b64urlJson(payload)}`;
  const sig = hmac(signingInput, secret).toString("base64url");
  return `${signingInput}.${sig}`;
}

export type VerifyResult =
  | { ok: true; claims: PtyTokenClaims }
  | { ok: false; reason: string };

export function verifyPtyToken(
  token: string,
  secret: string,
  opts: { wsid: string; nowS?: number },
): VerifyResult {
  const nowS = opts.nowS ?? Math.floor(Date.now() / 1000);
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
  if (header.alg !== PTY_JWT_ALG) {
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

  let claims: PtyTokenClaims;
  try {
    claims = JSON.parse(Buffer.from(p, "base64url").toString("utf8"));
  } catch {
    return { ok: false, reason: "bad payload" };
  }
  if (claims.aud !== PTY_JWT_AUD) return { ok: false, reason: "aud mismatch" };
  if (claims.iss !== PTY_JWT_ISS) return { ok: false, reason: "iss mismatch" };
  if (typeof claims.sub !== "string" || claims.sub.length === 0) {
    return { ok: false, reason: "missing sub" };
  }
  if (claims.wsid !== opts.wsid) return { ok: false, reason: "wsid mismatch" };
  if (typeof claims.exp !== "number" || nowS > claims.exp + CLOCK_SKEW_S) {
    return { ok: false, reason: "expired" };
  }
  return { ok: true, claims };
}
