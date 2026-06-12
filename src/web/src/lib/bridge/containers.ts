import "server-only";
import { execFile } from "node:child_process";
import { promises as fs } from "node:fs";
import { existsSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  appendSessionEvent,
  bumpWorkerEpoch,
  findSession,
  setSessionContainer,
  setSessionToken,
  type Store,
} from "./store";
import { getGithubToken } from "../connectors/github";

// Container-backed /code sessions (decisions-pending §12, modeled on
// claude.ai/code's init sequence):
//
//   ✓ Set up a cloud container
//   ✓ Cloned repository
//   ◌ Run setup script      (skipped unless the repo has .jarvis/setup.sh)
//   ✓ Started Claude Code
//
// One docker container per session from the jarvis-workbench image (passive
// `sleep infinity`; we exec the steps into it — same shape the workbench
// feature uses). The CLI source tree is bind-mounted read-only and run with
// its vendored bun; the child speaks the same /v1/code/sessions/{id}/worker
// endpoints a bridge-spawned child does, so everything downstream (SSE,
// prompts, permission cards, transcripts) is unchanged.
//
// MVP tradeoffs, documented in §12: --network=host (the web app binds
// 127.0.0.1 only, and the child must POST back to it; filesystem/process
// isolation is the goal of this phase — the egress-proxy phase changes
// this), and no setup-script snapshot caching (claude.ai skips setup when
// unconfigured too).

const IMAGE = process.env.JARVIS_WORKBENCH_IMAGE || "jarvis-workbench:latest";
const CONTAINER_LABEL = "com.jarvis.code-session";
/** Init-step exec budget. Clone + setup of real repos can be slow. */
const STEP_TIMEOUT_MS = 10 * 60 * 1000;

type ExecResult = { stdout: string; stderr: string };
export type DockerExec = (args: string[]) => Promise<ExecResult>;

const realDockerExec: DockerExec = (args) =>
  new Promise((resolve, reject) => {
    execFile(
      "docker",
      args,
      { timeout: STEP_TIMEOUT_MS, maxBuffer: 8 * 1024 * 1024 },
      (err, stdout, stderr) => {
        if (err) {
          reject(
            new Error(
              `docker ${args[0]} failed: ${String(stderr || err.message).slice(-400)}`,
            ),
          );
          return;
        }
        resolve({ stdout: String(stdout), stderr: String(stderr) });
      },
    );
  });

export function containerNameFor(sessionId: string): string {
  return `jarvis-code-${sessionId}`;
}

/** Repo root of this checkout (the web app runs from <root>/src/web). */
function jarvisRepoRoot(): string {
  if (process.env.JARVIS_REPO_ROOT) return process.env.JARVIS_REPO_ROOT;
  const guess = path.resolve(process.cwd(), "..", "..");
  return guess;
}

/**
 * Read one value from ~/.jarvis/keys.env (the canonical key store the CLI
 * launchers `source`). Values are written by jarvisKeysEnv.ts with a safe
 * charset — a plain line parse is the documented contract.
 */
async function keysEnvValue(name: string): Promise<string | null> {
  try {
    const raw = await fs.readFile(
      path.join(os.homedir(), ".jarvis", "keys.env"),
      "utf8",
    );
    for (const line of raw.split("\n")) {
      const m = /^([A-Z0-9_]+)=(.*)$/.exec(line.trim());
      if (m && m[1] === name && m[2]) return m[2];
    }
  } catch {
    /* no keys.env */
  }
  return null;
}

function emit(store: Store, sessionId: string, status: string): void {
  appendSessionEvent(store, sessionId, {
    type: "status",
    payload: { type: "status", status },
  });
}

/** `owner/name` → safe checkout dir name (`name`). */
function repoDirName(repoFullName: string): string {
  const name = repoFullName.split("/").pop() || "repo";
  return name.replace(/[^A-Za-z0-9._-]/g, "_");
}

export function validRepoFullName(repo: string): boolean {
  return /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo);
}

/**
 * Launch a container session: container → clone → optional setup → CLI.
 * Emits one status session_event per init step (the /code session view
 * renders status events as plain lines, so progress streams in like the
 * claude.ai "Initialized session" block). Throws on step failure AFTER
 * emitting the failure event and removing the container.
 */
export async function launchContainerSession(
  store: Store,
  opts: {
    sessionId: string;
    repoFullName: string;
    /** http://host:port — where the in-container child reaches this app. */
    baseUrl: string;
    exec?: DockerExec;
  },
): Promise<void> {
  const exec = opts.exec ?? realDockerExec;
  const { sessionId, repoFullName } = opts;
  const name = containerNameFor(sessionId);
  const dir = repoDirName(repoFullName);
  const workdir = `/workspace/${dir}`;

  // The session token + epoch the child authenticates with. The web is the
  // spawner here (environment-manager role): bump the epoch directly and
  // hand it to the child via env, like bridgeMain does via registerWorker.
  const session = findSession(store, sessionId);
  let token = session?.session_token ?? null;
  if (!token) {
    const { randomBytes } = await import("node:crypto");
    token = `sit_${randomBytes(24).toString("base64url")}`;
    setSessionToken(store, sessionId, token);
  }
  const epoch = bumpWorkerEpoch(store, sessionId);

  const anthropicKey =
    (await keysEnvValue("ANTHROPIC_API_KEY")) ||
    process.env.ANTHROPIC_API_KEY ||
    null;

  const cliMount = path.join(jarvisRepoRoot(), "src", "cli");
  if (!existsSync(path.join(cliMount, "src", "entrypoints", "cli.tsx"))) {
    emit(store, sessionId, `✗ Set up a cloud container — jarvis CLI source not found at ${cliMount} (set JARVIS_REPO_ROOT)`);
    throw new Error(`CLI source not found at ${cliMount}`);
  }

  const step = async (label: string, fn: () => Promise<void>): Promise<void> => {
    try {
      await fn();
      emit(store, sessionId, `✓ ${label}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      emit(store, sessionId, `✗ ${label} — ${msg.slice(0, 300)}`);
      await exec(["rm", "-f", name]).catch(() => {});
      throw err;
    }
  };

  // 1. Set up a cloud container
  await step("Set up a cloud container", async () => {
    // Reap any leftover container with this name (idempotent relaunch).
    await exec(["rm", "-f", name]).catch(() => {});
    await exec([
      "run",
      "-d",
      "--name",
      name,
      "--label",
      `${CONTAINER_LABEL}=${sessionId}`,
      "--network=host",
      "-v",
      `${cliMount}:/opt/jarvis-cli:ro`,
      IMAGE,
      "sleep",
      "infinity",
    ]);
    setSessionContainer(store, sessionId, {
      container: name,
      repo: repoFullName,
    });
  });

  // 2. Cloned repository
  await step("Cloned repository", async () => {
    const ghToken = await getGithubToken();
    const cloneUrl = ghToken
      ? `https://x-access-token:${ghToken}@github.com/${repoFullName}.git`
      : `https://github.com/${repoFullName}.git`;
    await exec(["exec", name, "git", "clone", cloneUrl, workdir]);
    // Scrub the token from .git/config — the checkout outlives the clone
    // credential. Pushing comes back in the branch/PR phase with a scoped
    // credential, mirroring claude.ai's git-proxy model.
    await exec([
      "exec",
      "-w",
      workdir,
      name,
      "git",
      "remote",
      "set-url",
      "origin",
      `https://github.com/${repoFullName}.git`,
    ]);
  });

  // 3. Run setup script (optional — skipped when the repo doesn't have one,
  // exactly like claude.ai's "Add a setup script to install dependencies").
  const probe = await exec([
    "exec",
    name,
    "sh",
    "-c",
    `test -f ${workdir}/.jarvis/setup.sh && echo yes || echo no`,
  ]);
  if (probe.stdout.trim() === "yes") {
    await step("Run setup script", async () => {
      await exec(["exec", "-w", workdir, name, "bash", ".jarvis/setup.sh"]);
    });
  } else {
    emit(store, sessionId, "◌ Run setup script — skipped (no .jarvis/setup.sh in the repo)");
  }

  // 4. Started Claude Code
  await step("Started Claude Code", async () => {
    // Pre-trust the workspace: bridge-spawned CLIs verify
    // projects[gitRoot].hasTrustDialogAccepted in the global config and
    // exit otherwise (no interactive dialog in --print mode).
    const config = JSON.stringify({
      hasCompletedOnboarding: true,
      projects: { [workdir]: { hasTrustDialogAccepted: true } },
    });
    await exec([
      "exec",
      name,
      "sh",
      "-c",
      `mkdir -p /jarvis-config && cat > /jarvis-config/.claude.json << 'JARVIS_EOF'\n${config}\nJARVIS_EOF`,
    ]);

    const childEnv: Record<string, string> = {
      CLAUDE_CONFIG_DIR: "/jarvis-config",
      CLAUDE_CODE_SESSION_ACCESS_TOKEN: token!,
      CLAUDE_CODE_USE_CCR_V2: "1",
      CLAUDE_CODE_WORKER_EPOCH: String(epoch),
      CLAUDE_CODE_ENVIRONMENT_KIND: "bridge",
      ...(anthropicKey && { ANTHROPIC_API_KEY: anthropicKey }),
    };
    const envArgs = Object.entries(childEnv).flatMap(([k, v]) => [
      "-e",
      `${k}=${v}`,
    ]);
    const sdkUrl = `${opts.baseUrl.replace(/\/+$/, "")}/api/bridge/v1/code/sessions/${sessionId}`;
    // Detached exec: the CLI runs for the session's lifetime; stdout goes
    // to an in-container log for debugging (docker exec <name> cat
    // /tmp/jarvis-cli.log). Vendored bun avoids version skew with the
    // image's bun; the MACRO runtime fallback in cli.tsx makes the direct
    // entrypoint launch safe without run-cli.mjs's --define args.
    await exec([
      "exec",
      "-d",
      "-w",
      workdir,
      ...envArgs,
      name,
      "sh",
      "-c",
      `/opt/jarvis-cli/vendor/bun/linux-x64/bun /opt/jarvis-cli/src/entrypoints/cli.tsx --print --sdk-url '${sdkUrl}' --session-id '${sessionId}' --input-format stream-json --output-format stream-json --replay-user-messages >> /tmp/jarvis-cli.log 2>&1`,
    ]);
  });
}

/** Stop + remove a session's container (archive path). Best-effort. */
export async function stopContainerSession(
  store: Store,
  sessionId: string,
  exec: DockerExec = realDockerExec,
): Promise<void> {
  const session = findSession(store, sessionId);
  const meta = session?.container_json
    ? (JSON.parse(session.container_json) as { container?: string })
    : null;
  const name = meta?.container ?? containerNameFor(sessionId);
  await exec(["rm", "-f", name]).catch(() => {});
}
