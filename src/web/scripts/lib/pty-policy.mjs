// Pure boot-time policy predicates for the PTY server (scripts/pty-server.mjs).
// Isolated here so they are unit-testable WITHOUT importing node-pty, whose
// native prebuild is absent in CI (the exact reason the #161 pty test couldn't
// load). No side effects, no I/O — just the two decisions.

// Whether the PTY websocket requires a per-session HS256 token. ON BY DEFAULT.
// The old rule (ON only if JARVIS_PTY_REQUIRE_AUTH === "1" or off-loopback)
// meant a plain `npm run dev` on loopback shipped an UNauthenticated PTY — a
// real $SHELL as the local user to anything that could reach the socket. Now
// auth is on unless you EXPLICITLY opt out on loopback via
// JARVIS_PTY_REQUIRE_AUTH=0; any other value (incl. garbage) fails secure to
// ON, and off-loopback is always ON.
export function computeRequireAuth(env, host) {
  if (env.JARVIS_PTY_REQUIRE_AUTH === "0") return host !== "127.0.0.1";
  return true;
}

// Resolve the shell mode after applying the host-shell policy. Host-shell
// ("local" mode — spawning $SHELL on the host directly) has none of the
// container isolation, so it is gated behind an explicit opt-in and FORCED off
// under JARVIS_REQUIRE_LOCAL_AUTH=1: the server refuses the connection rather
// than silently dropping to a host shell when no sandbox container is available.
// Returns { mode } to use, or { mode: "docker", error } to refuse.
export function resolveShellMode({ mode, allowHostShell, requireLocalAuth }) {
  const hostShellAllowed = allowHostShell && !requireLocalAuth;
  if (mode === "docker") return { mode: "docker" };
  if (hostShellAllowed) return { mode: "local" };
  return {
    mode: "docker",
    error: requireLocalAuth
      ? "host shell disabled under JARVIS_REQUIRE_LOCAL_AUTH (no sandbox container available)"
      : "host shell disabled — set JARVIS_PTY_ALLOW_HOST_SHELL=1 to allow (no sandbox container available)",
  };
}
