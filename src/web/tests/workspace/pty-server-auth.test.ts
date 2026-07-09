import { describe, expect, test } from "vitest";

// The two pure security predicates the PTY sidecar boots from. Extracted from
// scripts/pty-server.mjs so the default-on-auth + host-shell-gate posture can
// be pinned without spawning a websocket server (importing the .mjs is a no-op
// unless run as `node scripts/pty-server.mjs`).
import { computeRequireAuth, resolveShellMode } from "../../scripts/pty-server.mjs";

const LOOPBACK = "127.0.0.1";
const OFFBOX = "0.0.0.0";

describe("computeRequireAuth — token required by default (finding #4)", () => {
  test("default (env unset) requires auth on loopback", () => {
    expect(computeRequireAuth({}, LOOPBACK)).toBe(true);
  });

  test("default (env unset) requires auth off-loopback", () => {
    expect(computeRequireAuth({}, OFFBOX)).toBe(true);
  });

  test("JARVIS_PTY_REQUIRE_AUTH=1 requires auth on loopback", () => {
    expect(computeRequireAuth({ JARVIS_PTY_REQUIRE_AUTH: "1" }, LOOPBACK)).toBe(true);
  });

  test("opt-out (=0) is honored ONLY on loopback", () => {
    expect(computeRequireAuth({ JARVIS_PTY_REQUIRE_AUTH: "0" }, LOOPBACK)).toBe(false);
  });

  test("opt-out (=0) is IGNORED off-loopback — a LAN bind still requires auth", () => {
    expect(computeRequireAuth({ JARVIS_PTY_REQUIRE_AUTH: "0" }, OFFBOX)).toBe(true);
  });

  test("a garbage value is not treated as opt-out (fails secure)", () => {
    expect(computeRequireAuth({ JARVIS_PTY_REQUIRE_AUTH: "false" }, LOOPBACK)).toBe(true);
    expect(computeRequireAuth({ JARVIS_PTY_REQUIRE_AUTH: "yes" }, LOOPBACK)).toBe(true);
  });
});

describe("resolveShellMode — host-shell fallback is gated (finding #4)", () => {
  // explicit "docker" always maps to docker
  test('mode="docker" → docker, regardless of flags', () => {
    expect(
      resolveShellMode({ mode: "docker", allowHostShell: false, requireLocalAuth: true, dockerReady: false }),
    ).toEqual({ mode: "docker" });
  });

  // explicit "local" request
  test('mode="local" refused without JARVIS_PTY_ALLOW_HOST_SHELL', () => {
    const r = resolveShellMode({ mode: "local", allowHostShell: false, requireLocalAuth: false, dockerReady: false });
    expect(r.mode).toBe("docker");
    expect(r.error).toMatch(/JARVIS_PTY_ALLOW_HOST_SHELL/);
  });

  test('mode="local" allowed when JARVIS_PTY_ALLOW_HOST_SHELL=1 and not fail-closed', () => {
    expect(
      resolveShellMode({ mode: "local", allowHostShell: true, requireLocalAuth: false, dockerReady: false }),
    ).toEqual({ mode: "local" });
  });

  test('mode="local" force-refused when JARVIS_REQUIRE_LOCAL_AUTH=1, even with allow flag', () => {
    const r = resolveShellMode({ mode: "local", allowHostShell: true, requireLocalAuth: true, dockerReady: false });
    expect(r.mode).toBe("docker");
    expect(r.error).toMatch(/JARVIS_REQUIRE_LOCAL_AUTH=1/);
  });

  // auto-detect
  test("auto → docker when docker is reachable (no host shell needed)", () => {
    expect(
      resolveShellMode({ mode: "auto", allowHostShell: false, requireLocalAuth: false, dockerReady: true }),
    ).toEqual({ mode: "docker" });
  });

  test("auto + docker down → refuses host-shell fallback by DEFAULT (no allow flag)", () => {
    const r = resolveShellMode({ mode: "auto", allowHostShell: false, requireLocalAuth: false, dockerReady: false });
    expect(r.mode).toBe("docker");
    expect(r.error).toMatch(/host-shell fallback refused/);
  });

  test("auto + docker down + allow flag → falls back to host shell", () => {
    expect(
      resolveShellMode({ mode: "auto", allowHostShell: true, requireLocalAuth: false, dockerReady: false }),
    ).toEqual({ mode: "local" });
  });

  test("auto + docker down + allow flag but JARVIS_REQUIRE_LOCAL_AUTH=1 → still refused", () => {
    const r = resolveShellMode({ mode: "auto", allowHostShell: true, requireLocalAuth: true, dockerReady: false });
    expect(r.mode).toBe("docker");
    expect(r.error).toMatch(/JARVIS_REQUIRE_LOCAL_AUTH=1/);
  });

  test("the container posture (require-local-auth, no allow flag, docker up) → docker with no error", () => {
    expect(
      resolveShellMode({ mode: "auto", allowHostShell: false, requireLocalAuth: true, dockerReady: true }),
    ).toEqual({ mode: "docker" });
  });
});
