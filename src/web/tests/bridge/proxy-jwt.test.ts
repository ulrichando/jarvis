import { describe, expect, test } from "vitest";
import { createHmac } from "node:crypto";

import {
  PROXY_JWT_AUD,
  signProxyToken,
  verifyProxyToken,
} from "@/lib/bridge/proxyJwt";

const SECRET = "jarvis-test-secret-deterministic";

// Cross-impl known-answer vector. This exact token is also produced by the CLI
// proxy mirror (src/cli/src/proxy/proxyJwt.ts) for the same inputs — verified
// byte-identical at build time. If EITHER impl drifts, this literal stops
// matching, so a web-minted token would no longer verify in the local proxy.
const KNOWN =
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." +
  "eyJzdWIiOiJ1c2VyLTEyMyIsImF1ZCI6ImphcnZpcy1wcm94eSIsImlzcyI6ImphcnZpcy13ZWIiLCJpYXQiOjE3MDAwMDAwMDAsImV4cCI6MTcwMDAwMTAwMH0." +
  "FvDLfV9PP72lX_GEb8w3TkT7L8WT8US3PAqwKDPDnfg";

function signRaw(
  claims: Record<string, unknown>,
  secret = SECRET,
  header: Record<string, unknown> = { alg: "HS256", typ: "JWT" },
): string {
  const h = Buffer.from(JSON.stringify(header)).toString("base64url");
  const p = Buffer.from(JSON.stringify(claims)).toString("base64url");
  const sig = createHmac("sha256", secret)
    .update(`${h}.${p}`)
    .digest("base64url");
  return `${h}.${p}.${sig}`;
}

describe("proxyJwt (web mint side) — HS256 sign/verify", () => {
  test("sign matches the cross-impl known answer (CLI byte-compat)", () => {
    expect(
      signProxyToken({ sub: "user-123", ttlSeconds: 1000 }, SECRET, 1700000000),
    ).toBe(KNOWN);
  });

  test("round-trips a freshly minted token", () => {
    const tok = signProxyToken({ sub: "abc", ttlSeconds: 60 }, SECRET, 1000);
    const r = verifyProxyToken(tok, SECRET, 1000);
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.claims.sub).toBe("abc");
  });

  test("rejects the wrong secret", () => {
    const r = verifyProxyToken(KNOWN, "wrong-secret", 1700000500);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe("signature mismatch");
  });

  test("rejects alg=none", () => {
    const r = verifyProxyToken(
      signRaw(
        { sub: "x", aud: PROXY_JWT_AUD, iss: "jarvis-web", iat: 1, exp: 9999999999 },
        SECRET,
        { alg: "none", typ: "JWT" },
      ),
      SECRET,
      1000,
    );
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toContain("alg");
  });

  test("rejects an expired token", () => {
    const r = verifyProxyToken(KNOWN, SECRET, 1700001000 + 61);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe("expired");
  });

  test("rejects the wrong audience", () => {
    const r = verifyProxyToken(
      signRaw({ sub: "x", aud: "other", iss: "jarvis-web", iat: 1, exp: 9999999999 }),
      SECRET,
      1000,
    );
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toBe("aud mismatch");
  });

  test("rejects a tampered payload", () => {
    const [h, , s] = KNOWN.split(".");
    const forged = Buffer.from(
      JSON.stringify({
        sub: "attacker",
        aud: PROXY_JWT_AUD,
        iss: "jarvis-web",
        iat: 1700000000,
        exp: 1700001000,
      }),
    ).toString("base64url");
    const r = verifyProxyToken(`${h}.${forged}.${s}`, SECRET, 1700000500);
    expect(r.ok).toBe(false);
  });
});
