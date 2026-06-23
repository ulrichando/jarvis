import { describe, expect, test } from "vitest";
import { createHmac } from "node:crypto";

import {
  PTY_JWT_AUD,
  signPtyToken,
  verifyPtyToken,
} from "@/lib/workspace/ptyToken";
// The plain-Node twin the PTY sidecar (scripts/pty-server.mjs) actually runs.
// Importing it here is the whole point: the cross-impl vector below proves the
// .ts mint side and the .mjs verify side haven't drifted.
import { verifyPtyToken as verifyPtyTokenMjs } from "../../scripts/lib/pty-auth.mjs";

const SECRET = "jarvis-test-secret-deterministic";
const WSID = "ws-abc";

// Known-answer vector: signPtyToken({sub:"user-123", wsid:"ws-abc", ttl:1000},
// SECRET, 1700000000). If EITHER impl drifts, this literal stops matching and a
// web-minted token would no longer open a shell in the sidecar.
const KNOWN =
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." +
  "eyJzdWIiOiJ1c2VyLTEyMyIsIndzaWQiOiJ3cy1hYmMiLCJhdWQiOiJqYXJ2aXMtcHR5IiwiaXNzIjoiamFydmlzLXdlYiIsImlhdCI6MTcwMDAwMDAwMCwiZXhwIjoxNzAwMDAxMDAwfQ." +
  "v_-37lKKRbJAQ6zxlQLB5FSrWXZXfvMxTNz4r4TjGe4";

function signRaw(
  claims: Record<string, unknown>,
  secret = SECRET,
  header: Record<string, unknown> = { alg: "HS256", typ: "JWT" },
): string {
  const h = Buffer.from(JSON.stringify(header)).toString("base64url");
  const p = Buffer.from(JSON.stringify(claims)).toString("base64url");
  const sig = createHmac("sha256", secret).update(`${h}.${p}`).digest("base64url");
  return `${h}.${p}.${sig}`;
}

describe("ptyToken (web mint side) — HS256 sign/verify", () => {
  test("sign matches the known answer", () => {
    expect(
      signPtyToken({ sub: "user-123", wsid: WSID, ttlSeconds: 1000 }, SECRET, 1700000000),
    ).toBe(KNOWN);
  });

  test("round-trips a freshly minted token", () => {
    const tok = signPtyToken({ sub: "abc", wsid: WSID, ttlSeconds: 60 }, SECRET, 1000);
    const r = verifyPtyToken(tok, SECRET, { wsid: WSID, nowS: 1000 });
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.claims.wsid).toBe(WSID);
  });

  test("rejects a token scoped to a different workspace", () => {
    const r = verifyPtyToken(KNOWN, SECRET, { wsid: "ws-other", nowS: 1700000500 });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe("wsid mismatch");
  });

  test("rejects the wrong secret", () => {
    const r = verifyPtyToken(KNOWN, "wrong-secret", { wsid: WSID, nowS: 1700000500 });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe("signature mismatch");
  });

  test("rejects alg=none", () => {
    const r = verifyPtyToken(
      signRaw(
        { sub: "x", wsid: WSID, aud: PTY_JWT_AUD, iss: "jarvis-web", iat: 1, exp: 9999999999 },
        SECRET,
        { alg: "none", typ: "JWT" },
      ),
      SECRET,
      { wsid: WSID, nowS: 1000 },
    );
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toContain("alg");
  });

  test("rejects an expired token", () => {
    const r = verifyPtyToken(KNOWN, SECRET, { wsid: WSID, nowS: 1700001000 + 61 });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe("expired");
  });

  test("rejects a proxy-audience token (no cross-family reuse)", () => {
    const r = verifyPtyToken(
      signRaw({ sub: "x", wsid: WSID, aud: "jarvis-proxy", iss: "jarvis-web", iat: 1, exp: 9999999999 }),
      SECRET,
      { wsid: WSID, nowS: 1000 },
    );
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe("aud mismatch");
  });
});

describe("pty-auth.mjs (sidecar verify side) — byte-compat with the .ts", () => {
  test("the sidecar twin verifies a .ts-minted token", () => {
    const r = verifyPtyTokenMjs(KNOWN, SECRET, WSID, 1700000500);
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.claims.sub).toBe("user-123");
  });

  test("the sidecar twin enforces the workspace scope", () => {
    const r = verifyPtyTokenMjs(KNOWN, SECRET, "ws-other", 1700000500);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe("wsid mismatch");
  });

  test("the sidecar twin rejects a tampered payload", () => {
    const [h, , s] = KNOWN.split(".");
    const forged = Buffer.from(
      JSON.stringify({
        sub: "attacker",
        wsid: WSID,
        aud: PTY_JWT_AUD,
        iss: "jarvis-web",
        iat: 1700000000,
        exp: 1700001000,
      }),
    ).toString("base64url");
    const r = verifyPtyTokenMjs(`${h}.${forged}.${s}`, SECRET, WSID, 1700000500);
    expect(r.ok).toBe(false);
  });
});
