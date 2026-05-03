// Docker container manager for workbench sandboxes.
//
// Lives under scripts/lib so both the standalone PTY server (plain Node)
// and Next.js API routes (TypeScript) can share it. It's authored as plain
// ESM so the PTY server has zero build step.
//
// One container per workspace, name = `jarvis-ws-<workspaceId>`. The
// workspace dir is bind-mounted at /workspace. Container is `sleep
// infinity`; we `docker exec` into it for shell sessions and individual
// commands. Lifecycle: lazy-start on first use, destroyed on workspace
// delete or explicit stop.

import { spawn, execFile } from "node:child_process";
import { promisify } from "node:util";
import path from "node:path";
import os from "node:os";

const execFileP = promisify(execFile);

export const IMAGE = process.env.JARVIS_WORKBENCH_IMAGE ?? "jarvis-workbench:latest";
export const WORKSPACES_ROOT =
  process.env.JARVIS_WORKSPACES_ROOT ??
  path.join(os.homedir(), ".jarvis", "workspaces");

export function containerName(workspaceId) {
  return `jarvis-ws-${workspaceId}`;
}

async function dockerJson(args) {
  const { stdout } = await execFileP("docker", args, { maxBuffer: 4 * 1024 * 1024 });
  return JSON.parse(stdout);
}

async function dockerText(args) {
  try {
    const { stdout } = await execFileP("docker", args, { maxBuffer: 4 * 1024 * 1024 });
    return stdout.trim();
  } catch (e) {
    return null;
  }
}

export async function dockerAvailable() {
  try {
    await execFileP("docker", ["version", "--format", "{{.Server.Version}}"]);
    return true;
  } catch {
    return false;
  }
}

export async function imageExists() {
  const out = await dockerText(["images", "-q", IMAGE]);
  return Boolean(out);
}

// Returns { state: "running" | "stopped" | "absent", ports: { containerPort: hostPort } }
export async function inspect(workspaceId) {
  const name = containerName(workspaceId);
  const out = await dockerText([
    "inspect",
    "--format",
    "{{json .}}",
    name,
  ]);
  if (!out) return { state: "absent", ports: {} };
  let info;
  try {
    info = JSON.parse(out);
  } catch {
    return { state: "absent", ports: {} };
  }
  const running = info?.State?.Running === true;
  const portsRaw = info?.NetworkSettings?.Ports ?? {};
  const ports = {};
  for (const [key, bindings] of Object.entries(portsRaw)) {
    if (!Array.isArray(bindings) || bindings.length === 0) continue;
    // key looks like "5173/tcp"; pick first IPv4 binding
    const v4 = bindings.find((b) => b.HostIp === "0.0.0.0" || b.HostIp === "127.0.0.1") ?? bindings[0];
    if (v4?.HostPort) {
      const [containerPort] = key.split("/");
      ports[containerPort] = Number(v4.HostPort);
    }
  }
  return { state: running ? "running" : "stopped", ports };
}

export async function ensureRunning(workspaceId) {
  const name = containerName(workspaceId);
  const cur = await inspect(workspaceId);
  if (cur.state === "running") return cur;
  if (cur.state === "stopped") {
    await execFileP("docker", ["start", name]);
    return inspect(workspaceId);
  }

  // Absent — create fresh.
  const cwd = path.join(WORKSPACES_ROOT, workspaceId);
  await execFileP("mkdir", ["-p", cwd]);

  const uid = process.getuid?.() ?? 1000;
  const gid = process.getgid?.() ?? 1000;

  const args = [
    "run",
    "-d",
    "--name", name,
    "--label", "jarvis.workbench=1",
    "--label", `jarvis.workspaceId=${workspaceId}`,
    "-v", `${cwd}:/workspace`,
    "-w", "/workspace",
    "-P",                      // publish all EXPOSEd ports to random host ports
    "--memory", "2g",
    "--cpus", "2",
    "--pids-limit", "512",
    "--restart", "no",
    // Run init as root so `apt-get install` etc. works if the user wants
    // it. We still `exec` as the host UID below for file-ownership sanity.
    IMAGE,
    "sleep", "infinity",
  ];
  await execFileP("docker", args);
  return inspect(workspaceId);
}

export async function stop(workspaceId) {
  const name = containerName(workspaceId);
  await execFileP("docker", ["stop", "-t", "2", name]).catch(() => {});
}

export async function destroy(workspaceId) {
  const name = containerName(workspaceId);
  await execFileP("docker", ["rm", "-f", name]).catch(() => {});
}

// Run a one-shot command inside the workspace container. Returns
// { stdout, stderr, exitCode, durationMs }. For long-running processes
// (dev servers etc.) use spawnDetached() instead.
export async function exec(workspaceId, command, { timeoutMs = 600_000 } = {}) {
  await ensureRunning(workspaceId);
  const name = containerName(workspaceId);
  const uid = process.getuid?.() ?? 1000;
  const gid = process.getgid?.() ?? 1000;
  const start = Date.now();
  try {
    const { stdout, stderr } = await execFileP(
      "docker",
      [
        "exec",
        "-u", `${uid}:${gid}`,
        "-w", "/workspace",
        name,
        "/bin/bash", "-lc", command,
      ],
      { maxBuffer: 16 * 1024 * 1024, timeout: timeoutMs },
    );
    return { stdout, stderr, exitCode: 0, durationMs: Date.now() - start };
  } catch (e) {
    const err = e;
    return {
      stdout: err.stdout?.toString() ?? "",
      stderr: err.stderr?.toString() ?? err.message ?? "",
      exitCode: typeof err.code === "number" ? err.code : 1,
      durationMs: Date.now() - start,
    };
  }
}

// Start a detached long-running process (e.g., `npm run dev`). Returns
// immediately with the spawned exec ID. We redirect both streams into
// `.jarvis/dev.log` inside the workspace (bind-mounted, so the host can
// read it without another docker exec) — gives the model a tailable
// log for runtime diagnostics. Without this, dev-server output goes
// nowhere and the model is blind to crashes / 500s / port conflicts.
export async function spawnDetached(workspaceId, command) {
  await ensureRunning(workspaceId);
  const name = containerName(workspaceId);
  const uid = process.getuid?.() ?? 1000;
  const gid = process.getgid?.() ?? 1000;
  // Wrap the user command so the shell:
  //   1. ensures .jarvis/ exists
  //   2. timestamps the start so successive runs are easy to scan
  //   3. truncates the log (one dev server at a time → fresh log per start)
  //   4. runs the command with stdout+stderr appended to dev.log
  //   5. on exit, appends an EXIT marker with the code
  // The whole thing is `bash -lc` so users can still pipe / chain commands
  // in the action body — the wrapper just adds redirection.
  // Inline-export the polling env vars before running the user command.
  // We tried setting them via `docker exec -e` but they vanished between
  // bash and bun for reasons unclear (likely bun's env normalization).
  // Using `export` inside the wrapper bash is the shell-level way to
  // propagate vars through any chain of subshells and `bun run` invocations
  // — including past `cd … &&` prefixes the caller may chain.
  const wrapped = [
    "mkdir -p .jarvis",
    "export CHOKIDAR_USEPOLLING=true",
    "export CHOKIDAR_INTERVAL=300",
    "export WATCHPACK_POLLING=true",
    "export NEXT_TELEMETRY_DISABLED=1",
    `printf '\\n--- start %s ---\\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > .jarvis/dev.log`,
    `(${command}) >> .jarvis/dev.log 2>&1`,
    `echo "--- exit $? ---" >> .jarvis/dev.log`,
  ].join("; ");
  // -d puts docker exec in detached mode: it returns as soon as the
  // process is started, leaving it running inside the container.
  // Force file-watcher polling: Docker bind mounts on Linux don't reliably
  // propagate inotify events from host writes into the container, so
  // Next/Vite/Webpack's default fsnotify-based watchers miss host edits
  // and the dev server stops auto-reloading. Setting these env vars makes
  // every common watcher (chokidar, webpack, vite-plugin-node, nodemon)
  // fall back to polling, which DOES see bind-mount changes.
  const dockerArgs = [
    "exec",
    "-d",
    "-u", `${uid}:${gid}`,
    "-w", "/workspace",
    "-e", "CHOKIDAR_USEPOLLING=true",
    "-e", "CHOKIDAR_INTERVAL=300",
    "-e", "WATCHPACK_POLLING=true",
    "-e", "NEXT_TELEMETRY_DISABLED=1",
    name,
    "/bin/bash", "-lc", wrapped,
  ];
  console.log("[spawnDetached] cmd=docker", dockerArgs.slice(0, 12).join(" "));
  const { stdout } = await execFileP("docker", dockerArgs);
  return { execId: stdout.trim() };
}

// Spawn a docker exec for a PTY shell. Returns the spawned ChildProcess
// (or pty handle, depending on caller). For node-pty, use spawnExecArgs()
// with pty.spawn().
export function execShellArgs(workspaceId, { cols = 80, rows = 24 } = {}) {
  const name = containerName(workspaceId);
  const uid = process.getuid?.() ?? 1000;
  const gid = process.getgid?.() ?? 1000;
  return [
    "exec",
    "-it",
    "-u", `${uid}:${gid}`,
    "-w", "/workspace",
    "-e", `COLUMNS=${cols}`,
    "-e", `LINES=${rows}`,
    "-e", "TERM=xterm-256color",
    name,
    "/bin/bash",
  ];
}
