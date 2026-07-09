#!/usr/bin/env node
// Standalone WebSocket PTY server. Next.js can't host websockets cleanly,
// so the workbench terminal connects directly to ws://localhost:8772/pty
// while the main app stays on 3000.
//
// Two modes:
//   - "docker"  → ensure the workspace's container is running, then PTY-exec
//                 a bash inside it. Bind mount keeps files on the host fs.
//   - "local"   → spawn $SHELL on the host directly (Plan B fallback).
//
// Mode selection: JARVIS_WORKBENCH_MODE env var, or auto-detect (docker if
// daemon + image are reachable, else local).
//
// Protocol (text frames, JSON):
//   client → { type: "init", workspaceId, cols, rows }   first frame
//   client → { type: "input", data: string }
//   client → { type: "resize", cols, rows }
//   server → { type: "output", data: string }
//   server → { type: "exit", code }

import { WebSocketServer } from "ws";
import pty from "node-pty";
import path from "node:path";
import os from "node:os";
import fs from "node:fs";
import { pathToFileURL } from "node:url";
import {
  dockerAvailable,
  imageExists,
  ensureRunning,
  execShellArgs,
} from "./lib/docker.mjs";
import { verifyPtyToken, readPtyJwtSecret } from "./lib/pty-auth.mjs";

// ── Auth + mode policy (pure, unit-tested in tests/workspace/pty-server-auth.test.ts) ──
//
// These two predicates decide the whole security posture of the sidecar, so
// they're extracted from the boot code below to be testable without spawning a
// server. Defaults are SECURE: a token is required unless explicitly disabled,
// and the host-shell fallback is off unless explicitly allowed AND the box
// isn't in fail-closed local-auth mode.

/**
 * Whether the PTY websocket requires a valid per-session HS256 token.
 *
 * Default ON. Opt OUT only with JARVIS_PTY_REQUIRE_AUTH=0 (frictionless
 * `npm run dev` on a trusted loopback box). Off-loopback binds and an explicit
 * JARVIS_PTY_REQUIRE_AUTH=1 both force it on regardless — a co-located process
 * reaching :8772/pty must not get a credential-free shell.
 *
 * @param {Record<string, string | undefined>} env
 * @param {string} host  the resolved bind host
 * @returns {boolean}
 */
export function computeRequireAuth(env, host) {
  const flag = env.JARVIS_PTY_REQUIRE_AUTH;
  if (flag === "0") return host !== "127.0.0.1"; // opt-out honored ONLY on loopback
  return true; // default ON; "1" / unset / anything-else → require auth
}

/**
 * Resolve the terminal backing mode.
 *
 *   "docker" → PTY-exec inside the workspace sandbox container.
 *   "local"  → spawn $SHELL on the HOST (Plan B fallback — credential-free
 *              host shell to anyone who passes the socket auth).
 *
 * The host-shell fallback is now GATED: auto-detect + the raw "local" request
 * only reach it when JARVIS_PTY_ALLOW_HOST_SHELL=1, and it is force-disabled
 * whenever JARVIS_REQUIRE_LOCAL_AUTH=1 (the container's fail-closed posture) —
 * there, docker is the only path and its absence is an error, never a silent
 * drop to a host shell.
 *
 * @param {object} o
 * @param {string} o.mode              JARVIS_WORKBENCH_MODE ("auto"|"docker"|"local")
 * @param {boolean} o.allowHostShell   JARVIS_PTY_ALLOW_HOST_SHELL === "1"
 * @param {boolean} o.requireLocalAuth JARVIS_REQUIRE_LOCAL_AUTH === "1"
 * @param {boolean} o.dockerReady      docker daemon + image reachable
 * @returns {{mode: "docker"|"local", error?: string}}
 */
export function resolveShellMode({ mode, allowHostShell, requireLocalAuth, dockerReady }) {
  const hostShellAllowed = allowHostShell && !requireLocalAuth;

  if (mode === "docker") return { mode: "docker" };
  if (mode === "local") {
    return hostShellAllowed
      ? { mode: "local" }
      : {
          mode: "docker",
          error: requireLocalAuth
            ? "host-shell mode refused: JARVIS_REQUIRE_LOCAL_AUTH=1 (docker only)"
            : "host-shell mode refused: set JARVIS_PTY_ALLOW_HOST_SHELL=1 to allow it",
        };
  }
  // auto
  if (dockerReady) return { mode: "docker" };
  if (hostShellAllowed) return { mode: "local" };
  return {
    mode: "docker",
    error: requireLocalAuth
      ? "docker unavailable and host-shell fallback refused (JARVIS_REQUIRE_LOCAL_AUTH=1)"
      : "docker unavailable and host-shell fallback refused (set JARVIS_PTY_ALLOW_HOST_SHELL=1 to allow it)",
  };
}

// 8772 (NOT 8769): 8767-8769 is the voice-client status port block
// (jarvis/gemini/openai); jarvis-gpt-tools' status server owns 8769, and
// the desktop tray hardcodes it. Keep the PTY sidecar out of that range.
const PORT = Number(process.env.JARVIS_PTY_PORT ?? 8772);
// Bind 127.0.0.1 by default — pre-2026-05-17 this defaulted to
// 0.0.0.0 which exposed an unauthenticated PTY shell to every device
// on the LAN. Anyone on the WiFi could `wscat ws://192.168.x.x:8772/pty`
// and get a `$SHELL` session as the local user (no auth, no allowlist).
// The next.js app itself binds 127.0.0.1 in package.json scripts; this
// matches that posture. Set JARVIS_PTY_HOST explicitly to override for
// LAN-accessible workbench deployments — and add auth before doing so.
// Per enterprise plan §P0-SEC-5.
const HOST = process.env.JARVIS_PTY_HOST ?? "127.0.0.1";
const WORKSPACES_ROOT =
  process.env.JARVIS_WORKSPACES_ROOT ??
  path.join(os.homedir(), ".jarvis", "workspaces");

const SHELL = process.env.SHELL || "/bin/bash";
const MODE = process.env.JARVIS_WORKBENCH_MODE ?? "auto";
const ALLOW_HOST_SHELL = process.env.JARVIS_PTY_ALLOW_HOST_SHELL === "1";
const REQUIRE_LOCAL_AUTH = process.env.JARVIS_REQUIRE_LOCAL_AUTH === "1";

// Per-session auth on the websocket. The init frame must carry a short-lived
// HS256 token (minted by /api/workspace/[id]/pty-token, behind the app's auth)
// scoped to the workspace. Now DEFAULT ON (opt out only with
// JARVIS_PTY_REQUIRE_AUTH=0, and only on loopback) — a co-located process
// reaching :8772/pty must not get a credential-free shell. Off-loopback binds
// always require it. The sole client (components/workbench/terminal.tsx)
// already mints + sends the token on every connect, so default-on breaks no
// caller. Fails CLOSED: require auth but no secret on disk → reject every
// connection.
const REQUIRE_AUTH = computeRequireAuth(process.env, HOST);
// The secret is read LAZILY at verify time, not cached here: the web app shares
// this container and may create ~/.jarvis/keys.env on its first mint AFTER this
// process boots, and a lazy read also picks up rotation. Prefer setting
// JARVIS_PROXY_JWT_SECRET in the environment so both halves agree with no race.
if (REQUIRE_AUTH && !readPtyJwtSecret()) {
  console.error(
    "[pty-server] AUTH REQUIRED but JARVIS_PROXY_JWT_SECRET is unset " +
      "(env or ~/.jarvis/keys.env) — connections are rejected until it exists. " +
      "Set it in the environment, or open the /code terminal once so the web app mints it.",
  );
}

async function resolveMode() {
  const dockerReady = (await dockerAvailable()) && (await imageExists());
  const { mode, error } = resolveShellMode({
    mode: MODE,
    allowHostShell: ALLOW_HOST_SHELL,
    requireLocalAuth: REQUIRE_LOCAL_AUTH,
    dockerReady,
  });
  if (error) console.warn(`[pty-server] ${error}`);
  return { mode, error };
}

function startServer() {
  const wss = new WebSocketServer({ host: HOST, port: PORT, path: "/pty" });
  console.log(`[pty-server] listening on ws://${HOST}:${PORT}/pty (root=${WORKSPACES_ROOT})`);

  (async () => {
    const { mode: m } = await resolveMode();
    console.log(`[pty-server] mode=${m}${m === "docker" ? " (jarvis-workbench image found)" : ""}`);
  })();

  wss.on("connection", (ws) => {
  let term = null;
  let alive = true;

  const send = (obj) => {
    if (ws.readyState === ws.OPEN) ws.send(JSON.stringify(obj));
  };

  ws.on("message", async (raw) => {
    let msg;
    try {
      msg = JSON.parse(raw.toString());
    } catch {
      return;
    }

    if (msg.type === "init" && !term) {
      const id = String(msg.workspaceId || "");
      if (!id || !/^[a-z0-9-]+$/i.test(id)) {
        send({ type: "exit", code: 1, error: "bad workspace id" });
        try { ws.close(); } catch {}
        return;
      }

      if (REQUIRE_AUTH) {
        const secret = readPtyJwtSecret();
        const v = secret
          ? verifyPtyToken(String(msg.token || ""), secret, id)
          : { ok: false, reason: "server has no signing secret" };
        if (!v.ok) {
          console.warn(`[pty-server] rejected init for ${id}: ${v.reason}`);
          send({ type: "output", data: "\x1b[31m[terminal auth failed]\x1b[0m\r\n" });
          send({ type: "exit", code: 1, error: "unauthorized" });
          try { ws.close(); } catch {}
          return;
        }
      }

      const cwd = path.join(WORKSPACES_ROOT, id);
      try { fs.mkdirSync(cwd, { recursive: true }); } catch {}

      const cols = msg.cols || 80;
      const rows = msg.rows || 24;
      const { mode, error: modeError } = await resolveMode();

      // If docker was the only allowed backing and it's unreachable, refuse —
      // don't silently drop to (or spawn) a host shell.
      if (modeError && mode === "docker" && !(await dockerAvailable())) {
        send({ type: "output", data: `\x1b[31m[terminal unavailable: ${modeError}]\x1b[0m\r\n` });
        send({ type: "exit", code: 1, error: "no backing shell" });
        try { ws.close(); } catch {}
        return;
      }

      try {
        if (mode === "docker") {
          send({ type: "output", data: "\x1b[2m[starting sandbox container…]\x1b[0m\r\n" });
          await ensureRunning(id);
          const args = execShellArgs(id, { cols, rows });
          term = pty.spawn("docker", args, {
            name: "xterm-256color",
            cols,
            rows,
            cwd,
            env: { ...process.env, TERM: "xterm-256color" },
          });
        } else {
          term = pty.spawn(SHELL, [], {
            name: "xterm-256color",
            cols,
            rows,
            cwd,
            env: { ...process.env, TERM: "xterm-256color" },
          });
        }
      } catch (e) {
        send({ type: "output", data: `\x1b[31m[failed to start: ${e.message}]\x1b[0m\r\n` });
        send({ type: "exit", code: 1 });
        try { ws.close(); } catch {}
        return;
      }

      term.onData((data) => {
        if (alive) send({ type: "output", data });
      });
      term.onExit(({ exitCode }) => {
        send({ type: "exit", code: exitCode });
        try { ws.close(); } catch {}
      });
      return;
    }

    if (!term) return;

    if (msg.type === "input" && typeof msg.data === "string") {
      term.write(msg.data);
    } else if (msg.type === "resize") {
      try {
        term.resize(Number(msg.cols) || 80, Number(msg.rows) || 24);
      } catch {}
    }
  });

  ws.on("close", () => {
    alive = false;
    if (term) {
      try { term.kill(); } catch {}
    }
  });
  });
}

// Only start listening when run as a script (`node scripts/pty-server.mjs`),
// not when imported for its pure predicates (the unit tests import this file).
if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  startServer();
}
