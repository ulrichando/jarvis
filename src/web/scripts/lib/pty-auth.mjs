// Plain-Node twin of src/lib/workspace/ptyToken.ts (verify half) +
// src/lib/bridge/proxySecret.ts (secret read half). The PTY sidecar runs
// outside the Next bundle and can't import the TS modules, so the ~30 lines of
// HMAC verify are mirrored here — same boundary as scripts/lib/docker.mjs ↔
// src/lib/workspace/docker.ts. The known-answer vector in
// tests/workspace/pty-token.test.ts imports BOTH and fails on drift.
//
// The sidecar only ever READS the secret (the web app is the sole creator, via
// getOrCreateProxyJwtSecret on first mint). Fails closed: no secret → no verify.

import { createHmac, timingSafeEqual } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const PTY_JWT_ALG = "HS256";
const PTY_JWT_AUD = "jarvis-pty";
const PTY_JWT_ISS = "jarvis-web";
const CLOCK_SKEW_S = 60;
const SECRET_KEY = "JARVIS_PROXY_JWT_SECRET";
const LINE_KEY_RE = /^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=/;

/** Resolve the shared HMAC secret: env wins, else last match in ~/.jarvis/keys.env. */
export function readPtyJwtSecret() {
  const fromEnv = process.env[SECRET_KEY]?.trim();
  if (fromEnv) return fromEnv;
  const path = join(homedir(), ".jarvis", "keys.env");
  if (!existsSync(path)) return undefined;
  let value;
  for (const line of readFileSync(path, "utf8").split("\n")) {
    const m = LINE_KEY_RE.exec(line);
    if (m?.[1] === SECRET_KEY) value = line.slice(line.indexOf("=") + 1).trim();
  }
  return value || undefined;
}

function hmac(data, secret) {
  return createHmac("sha256", secret).update(data).digest();
}

/** Returns { ok: true, claims } | { ok: false, reason }. Byte-compatible with
 *  verifyPtyToken in src/lib/workspace/ptyToken.ts. */
export function verifyPtyToken(token, secret, wsid, nowS = Math.floor(Date.now() / 1000)) {
  if (!token || !secret) return { ok: false, reason: "missing token or secret" };
  const parts = token.split(".");
  if (parts.length !== 3) return { ok: false, reason: "malformed" };
  const [h, p, s] = parts;

  let header;
  try {
    header = JSON.parse(Buffer.from(h, "base64url").toString("utf8"));
  } catch {
    return { ok: false, reason: "bad header" };
  }
  if (header.alg !== PTY_JWT_ALG) {
    return { ok: false, reason: `unexpected alg ${String(header.alg)}` };
  }

  const expected = hmac(`${h}.${p}`, secret);
  let given;
  try {
    given = Buffer.from(s, "base64url");
  } catch {
    return { ok: false, reason: "bad signature encoding" };
  }
  if (given.length !== expected.length || !timingSafeEqual(given, expected)) {
    return { ok: false, reason: "signature mismatch" };
  }

  let claims;
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
  if (claims.wsid !== wsid) return { ok: false, reason: "wsid mismatch" };
  if (typeof claims.exp !== "number" || nowS > claims.exp + CLOCK_SKEW_S) {
    return { ok: false, reason: "expired" };
  }
  return { ok: true, claims };
}
