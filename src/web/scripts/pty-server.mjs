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
import {
  dockerAvailable,
  imageExists,
  ensureRunning,
  execShellArgs,
} from "./lib/docker.mjs";
import { verifyPtyToken, readPtyJwtSecret } from "./lib/pty-auth.mjs";

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
let MODE = process.env.JARVIS_WORKBENCH_MODE ?? "auto";

// Per-session auth on the websocket. The init frame must carry a short-lived
// HS256 token (minted by /api/workspace/[id]/pty-token, behind the app's auth)
// scoped to the workspace. Required automatically whenever the socket is bound
// off-loopback (the documented foot-gun this guards) or forced on via env —
// so exposing the sidecar can't accidentally ship an open root shell. On
// loopback it's optional, preserving the frictionless `npm run dev` posture.
// Fails CLOSED: require auth but no secret on disk → reject every connection.
const REQUIRE_AUTH =
  process.env.JARVIS_PTY_REQUIRE_AUTH === "1" || HOST !== "127.0.0.1";
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
  if (MODE === "local" || MODE === "docker") return MODE;
  // auto-detect
  if ((await dockerAvailable()) && (await imageExists())) return "docker";
  return "local";
}

const wss = new WebSocketServer({ host: HOST, port: PORT, path: "/pty" });
console.log(`[pty-server] listening on ws://${HOST}:${PORT}/pty (root=${WORKSPACES_ROOT})`);

(async () => {
  const m = await resolveMode();
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
      const mode = await resolveMode();

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
