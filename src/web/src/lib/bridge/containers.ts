import "server-only";
import { execFile } from "node:child_process";
import { promises as fs } from "node:fs";
import { existsSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  appendSessionEvent,
  bumpWorkerEpoch,
  clearSessionContainer,
  findEnvironment,
  findSession,
  getWorkerSpec,
  parseEnvironmentConfig,
  resumeFloorSeq,
  setInboundFloorSeq,
  setSessionContainer,
  setSessionToken,
  setWorkerSpec,
  type Store,
} from "./store";
import { getGithubToken, githubStatus } from "../connectors/github";
import { MODELS_META } from "../ai/models-meta";
import { listMcpServers } from "../mcp/store";

// Container-backed /code sessions (decisions-pending §12, modeled on
// claude.ai/code's init sequence):
//
//   ✓ Set up a cloud container
//   ✓ Cloned repository
//   ◌ Run setup script      (skipped unless the repo has .jarvis/setup.sh)
//   ✓ Started Jarvis Code
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
/** Egress allowlist proxy image (squid). Pinnable via env. */
const EGRESS_PROXY_IMAGE = process.env.JARVIS_EGRESS_PROXY_IMAGE || "ubuntu/squid:latest";
/** Domains a `trusted`/`custom` egress level always allows (package registries
 *  + GitHub), mirroring claude.ai/code's default-allowed list. */
const DEFAULT_ALLOW = [
  ".github.com",
  ".githubusercontent.com",
  ".npmjs.org",
  "registry.npmjs.org",
  "pypi.org",
  "files.pythonhosted.org",
  "crates.io",
  "static.crates.io",
  ".rubygems.org",
  ".debian.org",
  ".ubuntu.com",
  "host.docker.internal",
];

/** Generate a squid forward-proxy config that allows CONNECT/HTTP only to the
 *  given domains and denies everything else (empty list = deny all). */
function buildSquidConf(domains: string[]): string {
  const acls = domains.map((d) => `acl allowed dstdomain ${d}`).join("\n");
  return [
    "http_port 3128",
    acls,
    domains.length ? "http_access allow allowed" : "",
    "http_access deny all",
  ]
    .filter(Boolean)
    .join("\n");
}
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
 * POSIX single-quote a value for safe embedding in a `sh -c` command. The
 * GitHub token / login flow through this when we configure git credentials
 * in-container, so a stray quote can't break out of the command.
 */
function shq(s: string): string {
  return `'${s.replace(/'/g, `'\\''`)}'`;
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
    /** The model the user picked (a MODELS_META id). Routed through the local
     *  proxy when it is up (any provider), else `--model` for Claude-direct. */
    model?: string;
    /** Additional repos cloned alongside the primary (multi-repo session) into
     *  /workspace/<name> each. The primary stays the workdir for diff/PR. */
    extraRepos?: string[];
    exec?: DockerExec;
    /** Probe for the local model proxy on :4000. Injectable for tests; the
     *  default hits <proxy>/health. */
    proxyHealthy?: () => Promise<boolean>;
    /** Per-session connector allow-list (MCP server ids). When provided, ONLY
     *  these are attached — intersected with the globally-enabled set, so it
     *  can never grant a disabled one. `[]` attaches none. `undefined` (routines
     *  / legacy callers) keeps the back-compat behavior of attaching every
     *  globally-enabled connector. The web /code UI always sends an explicit
     *  array (opt-in; empty by default), so no connector rides along unasked. */
    connectors?: string[];
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
  // Per-environment config (claude.ai/code env config): extra env vars + an
  // optional setup script, applied below.
  const env = session?.environment_id
    ? findEnvironment(store, session.environment_id)
    : null;
  const envConfig = parseEnvironmentConfig(env);
  const extraRepos = (opts.extraRepos ?? []).filter(validRepoFullName);
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

  // ── Setup-snapshot caching (claude.ai/code env cache) ──────────────────
  // Env-gated (JARVIS_CODE_SETUP_CACHE=1, default OFF → the flow below is
  // byte-for-byte today's). When on and the env has a setup script, the first
  // session commits the post-setup container to a cache image keyed on the env
  // + setup-script hash; later sessions launch FROM it and skip clone + setup
  // (just freshen the repo + re-write creds), like claude.ai's ~7d snapshot.
  const cacheEnabled = process.env.JARVIS_CODE_SETUP_CACHE === "1";
  const hasEnvSetup = !!envConfig.setupScript.trim();
  let cacheTag: string | null = null;
  let cacheHit = false;
  // Caching is keyed on env + setup script only, so skip it for multi-repo
  // sessions (the extra repos aren't part of the cache key).
  if (cacheEnabled && env && hasEnvSetup && extraRepos.length === 0) {
    const { createHash } = await import("node:crypto");
    const key = `${env.environment_id}-${createHash("sha1").update(envConfig.setupScript).digest("hex").slice(0, 12)}`;
    cacheTag = `jarvis-workbench-cache:${key}`;
    cacheHit = await exec(["image", "inspect", cacheTag]).then(() => true).catch(() => false);
  }
  const runImage = cacheHit ? cacheTag! : IMAGE;

  // ── Egress policy (claude.ai/code network access) ──────────────────────
  // `full` (default) = today's --network=host, no proxy → zero regression.
  // Other levels run the workbench on a private bridge network whose only
  // egress is an allowlist squid proxy; the child reaches this app via
  // host.docker.internal (NO_PROXY) instead of 127.0.0.1.
  const netLevel = envConfig.networkLevel;
  const isolated = netLevel !== "full";
  const netName = `jarvis-net-${sessionId}`;
  const proxyName = `jarvis-egress-${sessionId}`;
  const netArgs = isolated
    ? ["--network", netName, "--add-host=host.docker.internal:host-gateway"]
    : ["--network=host"];
  // When isolated the child cannot use 127.0.0.1 for the callback — swap it for
  // the host gateway alias.
  const childBaseUrl = isolated
    ? opts.baseUrl.replace(/\/\/(?:127\.0\.0\.1|localhost)(:|\/|$)/, "//host.docker.internal$1")
    : opts.baseUrl;

  // 1. Set up a cloud container
  await step("Set up a cloud container", async () => {
    // Reap any leftover container with this name (idempotent relaunch).
    await exec(["rm", "-f", name]).catch(() => {});
    if (isolated) {
      // Private network + allowlist proxy. Best-effort setup; the workbench run
      // below still attaches to the network either way.
      await exec(["network", "create", netName]).catch(() => {});
      await exec(["rm", "-f", proxyName]).catch(() => {});
      await exec([
        "run",
        "-d",
        "--name",
        proxyName,
        "--network",
        netName,
        "--label",
        `${CONTAINER_LABEL}=${sessionId}`,
        EGRESS_PROXY_IMAGE,
      ]).catch(() => {});
      const allow =
        netLevel === "none"
          ? []
          : [...DEFAULT_ALLOW, ...(netLevel === "custom" ? envConfig.customAllowlist : [])];
      const conf = buildSquidConf(allow);
      await exec([
        "exec",
        proxyName,
        "sh",
        "-c",
        `cat > /etc/squid/squid.conf << 'JARVIS_EOF'\n${conf}\nJARVIS_EOF`,
      ]).catch(() => {});
      await exec(["exec", proxyName, "sh", "-c", "squid -k reconfigure 2>/dev/null || true"]).catch(
        () => {},
      );
      emit(store, sessionId, `◌ Network — ${netLevel} (egress via allowlist proxy)`);
    }
    await exec([
      "run",
      "-d",
      "--name",
      name,
      "--label",
      `${CONTAINER_LABEL}=${sessionId}`,
      ...netArgs,
      "-v",
      `${cliMount}:/opt/jarvis-cli:ro`,
      runImage,
      "sleep",
      "infinity",
    ]);
    setSessionContainer(store, sessionId, {
      container: name,
      repo: repoFullName,
    });
    if (cacheHit) emit(store, sessionId, "◌ Restored cached environment (setup skipped)");
  });

  // 2. Cloned repository — and make git fully push-capable, so the agent can
  // commit/branch/push/PR on its own without ever asking the user for a name,
  // email, or credentials.
  const ghToken = await getGithubToken();
  const gh = await githubStatus();

  // Committer identity + a store-backed push credential so `git commit`/`push`
  // work non-interactively. Runs on EVERY launch — cache restores scrub the
  // baked-in token (below) and tokens rotate. login/token are shq()-quoted so
  // they can't break out of the sh -c. Non-fatal: a hiccup warns (the session
  // can still read/edit) rather than aborting the launch.
  const configureGitCreds = async (): Promise<void> => {
    if (!(ghToken && gh.connected && gh.login)) return;
    const login = gh.login;
    const email = `${login}@users.noreply.github.com`;
    const credLine = `https://x-access-token:${ghToken}@github.com`;
    const cmd = [
      `git config --global user.name ${shq(login)}`,
      `git config --global user.email ${shq(email)}`,
      `git config --global credential.helper store`,
      `git config --global init.defaultBranch main`,
      `git config --global --add safe.directory ${shq(workdir)}`,
      `(umask 077; printf '%s\\n' ${shq(credLine)} > "$HOME/.git-credentials")`,
    ].join(" && ");
    try {
      await exec(["exec", name, "sh", "-c", cmd]);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      emit(store, sessionId, `⚠ git push credentials not configured — ${msg.slice(0, 200)}`);
    }
  };

  // 2. Cloned repository (or, on a cache hit, freshen the baked-in checkout).
  await step(cacheHit ? "Restored repository" : "Cloned repository", async () => {
    if (cacheHit) {
      // Repo + deps are baked into the cache image — bring the checkout up to
      // the latest default branch rather than re-cloning.
      await exec([
        "exec",
        "-w",
        workdir,
        name,
        "sh",
        "-c",
        `base=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##'); [ -z "$base" ] && base=main; git fetch origin >/dev/null 2>&1; git checkout "$base" >/dev/null 2>&1; git reset --hard "origin/$base" >/dev/null 2>&1; git clean -fd >/dev/null 2>&1`,
      ]).catch(() => {});
    } else {
      const cloneUrl = ghToken
        ? `https://x-access-token:${ghToken}@github.com/${repoFullName}.git`
        : `https://github.com/${repoFullName}.git`;
      await exec(["exec", name, "git", "clone", cloneUrl, workdir]);
      // Keep the remote URL clean (no embedded token — it would leak into
      // .git/config). Auth comes from the credential helper instead.
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
    }
    await configureGitCreds();
    // Multi-repo: clone each additional repo into /workspace/<name>. Global git
    // creds (above) cover pushes for all of them; the primary stays the workdir.
    for (const extra of extraRepos) {
      const edir = `/workspace/${repoDirName(extra)}`;
      const eurl = ghToken
        ? `https://x-access-token:${ghToken}@github.com/${extra}.git`
        : `https://github.com/${extra}.git`;
      await exec(["exec", name, "git", "clone", eurl, edir]).catch(() => {});
      await exec([
        "exec",
        "-w",
        edir,
        name,
        "git",
        "remote",
        "set-url",
        "origin",
        `https://github.com/${extra}.git`,
      ]).catch(() => {});
    }
    if (extraRepos.length) {
      emit(store, sessionId, `◌ Also cloned: ${extraRepos.join(", ")}`);
    }
  });

  // 2b–3. Setup scripts. Skipped entirely on a cache hit (baked into the image).
  if (cacheHit) {
    emit(store, sessionId, "◌ Setup — skipped (restored from cache)");
  } else {
    // 2b. Environment setup script (claude.ai/code env config) — runs before the
    // repo's optional .jarvis/setup.sh. Quoted heredoc so multi-line scripts +
    // special chars are safe (no shell expansion at write time).
    if (envConfig.setupScript.trim()) {
      await step("Run environment setup", async () => {
        await exec([
          "exec",
          name,
          "sh",
          "-c",
          `cat > /tmp/jarvis-env-setup.sh << 'JARVIS_EOF'\n${envConfig.setupScript}\nJARVIS_EOF`,
        ]);
        await exec(["exec", "-w", workdir, name, "bash", "/tmp/jarvis-env-setup.sh"]);
      });
    }

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

    // Snapshot the post-setup container for next time (env-gated). Scrub the
    // baked-in push token first (don't bake a credential into the image), then
    // re-write it for THIS session. Non-fatal — caching is an optimization.
    if (cacheEnabled && hasEnvSetup && cacheTag) {
      try {
        await exec(["exec", name, "sh", "-c", `rm -f "$HOME/.git-credentials"`]).catch(() => {});
        await exec(["commit", name, cacheTag]);
        await configureGitCreds();
        emit(store, sessionId, "◌ Cached environment snapshot for faster next launch");
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        emit(store, sessionId, `⚠ environment cache snapshot failed — ${msg.slice(0, 150)}`);
      }
    }
  }

  // 4. Started Jarvis Code
  await step("Started Jarvis Code", async () => {
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

    // MCP connectors (claude.ai/code "Connectors"): inject the user's enabled
    // HTTP/SSE MCP servers (~/.jarvis/mcp.json) as a project mcp config so the
    // session can use them. stdio (command) servers are skipped — their binary
    // isn't in the container.
    let mcpArg = "";
    try {
      const enabled = (await listMcpServers()).filter((s) => s.enabled && s.url);
      // Per-session allow-list: when the caller passes `connectors` (the web
      // /code UI always does — possibly empty), attach only that subset. When
      // it's undefined (routines / legacy callers), attach every enabled server
      // (back-compat). Intersecting with `enabled` means a stale id can never
      // grant a globally-disabled connector.
      const allow = opts.connectors;
      const servers = allow ? enabled.filter((s) => allow.includes(s.id)) : enabled;
      if (servers.length) {
        const mcpServers: Record<string, unknown> = {};
        for (const s of servers) {
          mcpServers[s.name] = {
            type: s.transport === "sse" ? "sse" : "http",
            url: s.url,
            ...(s.headers && Object.keys(s.headers).length ? { headers: s.headers } : {}),
          };
        }
        const mcpJson = JSON.stringify({ mcpServers });
        await exec([
          "exec",
          name,
          "sh",
          "-c",
          `cat > /jarvis-config/.mcp.json << 'JARVIS_EOF'\n${mcpJson}\nJARVIS_EOF`,
        ]);
        mcpArg = " --mcp-config /jarvis-config/.mcp.json";
        emit(store, sessionId, `◌ Connectors — ${servers.map((s) => s.name).join(", ")}`);
      }
    } catch {
      /* connectors are optional */
    }

    // ── Model routing ──────────────────────────────────────────────────
    // The jarvis CLI reaches every provider (DeepSeek/Groq/OpenAI/Gemini AND
    // Claude) through a local LiteLLM proxy on :4000 — the same one bin/jarvis
    // uses (src/cli/scripts/start.sh). The container runs --network=host, so it
    // can hit the host's 127.0.0.1:4000. When the proxy is up we mirror
    // start.sh's env so a /code session uses the SAME provider/model as the CLI
    // (DeepSeek v4 Pro by default); when it's down we fall back to talking to
    // api.anthropic.com directly (Claude only). The web model ids ARE the CLI
    // registry ids verbatim, so the picked id passes straight through.
    const CLI_DEFAULT_MODEL = "deepseek-v4-pro"; // bin/jarvis default
    const meta = opts.model ? MODELS_META[opts.model] : undefined;
    // Host-side URL (the web server probes this); the child reaches the same
    // proxy at host.docker.internal on an isolated network.
    const proxyHealthUrl = process.env.JARVIS_CLI_PROXY_URL || "http://127.0.0.1:4000";
    const proxyUrl = isolated
      ? proxyHealthUrl.replace(/\/\/(?:127\.0\.0\.1|localhost)(:|\/|$)/, "//host.docker.internal$1")
      : proxyHealthUrl;
    const probe =
      opts.proxyHealthy ??
      (() =>
        fetch(`${proxyHealthUrl}/health`, { signal: AbortSignal.timeout(2500) })
          .then((r) => r.ok)
          .catch(() => false));
    const proxyUp = await probe();
    let modelArg = "";
    const routingEnv: Record<string, string> = {};
    if (proxyUp) {
      // Picked model → its provider; nothing picked (or unknown id) → the CLI
      // default (DeepSeek v4 Pro), so a web session matches `bin/jarvis`.
      const proxyModel = meta ? opts.model! : CLI_DEFAULT_MODEL;
      const provider = meta?.provider ?? "deepseek";
      routingEnv.ANTHROPIC_BASE_URL = proxyUrl;
      routingEnv.ANTHROPIC_API_KEY = "jarvis-proxy"; // proxy holds the real keys
      routingEnv.JARVIS_PROVIDER = provider;
      routingEnv.JARVIS_MODEL = proxyModel;
      routingEnv.JARVIS_MODEL_REGISTRY_ENABLED = "1";
      routingEnv.JARVIS_DISABLE_AUTH = "1";
      routingEnv.ENABLE_TOOL_SEARCH = "true";
      // Non-Claude backends don't speak the ToolSearch deferral protocol.
      routingEnv.JARVIS_DISABLE_TOOL_DEFERRAL = "1";
      emit(store, sessionId, `◌ Model — ${proxyModel} (${provider}) via local proxy`);
    } else {
      // Proxy down → talk to api.anthropic.com directly. Only Claude runs;
      // a non-Claude pick warns and falls back to the default Claude model.
      if (anthropicKey) routingEnv.ANTHROPIC_API_KEY = anthropicKey;
      if (opts.model && meta?.provider === "anthropic") {
        modelArg = ` --model ${shq(opts.model)}`;
      } else if (opts.model) {
        emit(
          store,
          sessionId,
          `⚠ ${opts.model}${meta ? ` (${meta.provider})` : ""} needs the local model proxy (offline) — using the default Claude model.`,
        );
      }
    }

    const childEnv: Record<string, string> = {
      // User-configured env vars first, so the worker-handshake + routing keys
      // below always win over anything the user set with the same name.
      ...envConfig.envVars,
      CLAUDE_CONFIG_DIR: "/jarvis-config",
      CLAUDE_CODE_SESSION_ACCESS_TOKEN: token!,
      CLAUDE_CODE_USE_CCR_V2: "1",
      CLAUDE_CODE_WORKER_EPOCH: String(epoch),
      CLAUDE_CODE_ENVIRONMENT_KIND: "bridge",
      // The browser-facing session URL, so the agent can link PRs back to it.
      JARVIS_SESSION_URL: `${opts.baseUrl.replace(/\/+$/, "")}/code/session_${sessionId}`,
      // Global-scope prompt caching (an experimental firstParty beta) emits
      // `cache_control.scope: "global"` on system blocks that aren't a true
      // prefix when tool definitions render first — the API 400s the whole
      // turn ("only valid when every preceding block is also globally
      // scoped"). Normal ephemeral caching still applies with betas off.
      CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS: "1",
      ...routingEnv,
      // Egress: when isolated, route the child's HTTP(S) through the allowlist
      // proxy; the callback + model proxy bypass it via NO_PROXY.
      ...(isolated && {
        HTTP_PROXY: `http://${proxyName}:3128`,
        HTTPS_PROXY: `http://${proxyName}:3128`,
        http_proxy: `http://${proxyName}:3128`,
        https_proxy: `http://${proxyName}:3128`,
        NO_PROXY: "host.docker.internal,localhost,127.0.0.1",
        no_proxy: "host.docker.internal,localhost,127.0.0.1",
      }),
      // Authenticate the gh CLI (PR creation) the same token git pushes with.
      // git itself auths via the credential helper configured at clone time;
      // gh reads GH_TOKEN/GITHUB_TOKEN, so no `gh auth login` is needed.
      ...(ghToken && { GH_TOKEN: ghToken, GITHUB_TOKEN: ghToken }),
    };
    const envArgs = Object.entries(childEnv).flatMap(([k, v]) => [
      "-e",
      `${k}=${v}`,
    ]);
    const sdkUrl = `${childBaseUrl.replace(/\/+$/, "")}/api/bridge/v1/code/sessions/${sessionId}`;
    // Identity reinforcement + git workflow: the base system prompt already
    // says "You are Jarvis", but Opus/Sonnet have a strong "Claude Code" prior
    // and leak it when greeting. We also teach the agent that git is fully
    // wired here so it commits/pushes/PRs proactively instead of asking for a
    // name/email (the failure the user hit). Append rather than editing
    // src/cli's prompt (separate codebase). This is single-quoted in the sh -c
    // below, so it MUST contain no single quotes / apostrophes.
    const identityPrompt =
      "Your name is Jarvis. Never refer to yourself as Claude, Claude Code, or an Anthropic CLI in user-facing replies; introduce yourself and sign off as Jarvis. " +
      "This workspace is a clone of the selected GitHub repository and git is fully configured here: user.name and user.email are already set, and a credential helper supplies the GitHub push token, so git commit and git push both work without any prompting. " +
      "Never ask for a git name, email, or credentials, and never claim you are unable to commit or push. " +
      "When you make code changes worth keeping, save them with git proactively: create a branch named jarvis/<short-topic>, stage the changes, commit with a clear concise message, and run git push -u origin <branch>. " +
      "For substantial work also open a pull request: the gh CLI is authenticated, so run gh pr create with a title and body; if gh is unavailable, push the branch and share the pull-request URL for it instead. " +
      "When you open a pull request, include the session link from the JARVIS_SESSION_URL environment variable at the bottom of the PR body for traceability. " +
      "Do all of this automatically whenever you finish a unit of work or the user asks you to save, commit, merge, push, or open a PR; never reply that you were not asked to. " +
      "You are running inside an isolated container that is yours to use fully, so act autonomously like a senior engineer rather than hand-holding. " +
      "Run every command yourself with the Bash tool — install dependencies, run scripts, execute tests — and never tell the user to run a command or to install something; if a package is missing, install it and continue. " +
      "When the user asks for a file or a script, create it and write a complete working implementation instead of asking what to put in it: make reasonable assumptions, state them in one short line, and proceed. " +
      "Only ask the user a question when the request is genuinely ambiguous or the action is destructive; otherwise just do the work and report what you did.";
    // Detached exec: the CLI runs for the session's lifetime; stdout goes
    // to an in-container log for debugging (docker exec <name> cat
    // /tmp/jarvis-cli.log). Vendored bun avoids version skew with the
    // image's bun; the MACRO runtime fallback in cli.tsx makes the direct
    // entrypoint launch safe without run-cli.mjs's --define args.
    const workerCmd = `/opt/jarvis-cli/vendor/bun/linux-x64/bun /opt/jarvis-cli/src/entrypoints/cli.tsx --print --sdk-url '${sdkUrl}' --session-id '${sessionId}'${modelArg}${mcpArg} --append-system-prompt '${identityPrompt}' --input-format stream-json --output-format stream-json --replay-user-messages --include-partial-messages >> /tmp/jarvis-cli.log 2>&1`;
    // Persist the exact launch spec so a worker that later dies (e.g. a
    // web-server restart drops its SSE connection, or a crash) can be re-exec'd
    // into this still-running container on reopen — see resumeContainerWorker.
    // Re-running the same command resumes the same CLI session (its cursor is
    // persisted in CLAUDE_CONFIG_DIR), so it does NOT replay the original task.
    setWorkerSpec(store, sessionId, { env: childEnv, cmd: workerCmd, workdir });
    await exec([
      "exec",
      "-d",
      "-w",
      workdir,
      ...envArgs,
      name,
      "sh",
      "-c",
      workerCmd,
    ]);
  });
}

/**
 * Re-exec a dead session worker into its still-running container, using the
 * spec captured at launch. Powers auto-resume-on-reopen: a web-server restart
 * (or crash) kills the worker process, but the container + working tree
 * survive — re-running the same CLI command reconnects it and resumes the same
 * session. The CLI persists its own cursor (CLAUDE_CONFIG_DIR), so the original
 * task is NOT replayed. Returns true iff a worker was (re)started.
 */
export async function resumeContainerWorker(
  store: Store,
  sessionId: string,
  execArg?: DockerExec,
): Promise<boolean> {
  const exec = execArg ?? realDockerExec;
  const spec = getWorkerSpec(store, sessionId);
  if (!spec) return false;
  const session = findSession(store, sessionId);
  if (!session?.container_json || session.archived) return false;
  let name: string | undefined;
  try {
    name = (JSON.parse(session.container_json) as { container?: string }).container;
  } catch {
    return false;
  }
  if (!name) return false;
  // The container must still be running to exec into it (reclaim/stop removes
  // it; then there is nothing to resume).
  const running = await exec(["inspect", "--format", "{{.State.Running}}", name])
    .then((r) => r.stdout.trim() === "true")
    .catch(() => false);
  if (!running) return false;
  // Already-alive worker → nothing to do (idempotent; safe to call on every
  // reopen). Match by process name (comm = "bun") and EXCLUDE zombies: a killed
  // worker reparented to the container's `sleep infinity` PID 1 lingers as an
  // unreaped <defunct> (state Z), which `pgrep` would still match — so resume
  // would wrongly believe the worker is alive and never relaunch. (Matching the
  // cli.tsx path with `pgrep -f` is also wrong: it self-matches this very
  // `sh -c …` wrapper.)
  const liveWorkers = await exec([
    "exec",
    name,
    "sh",
    "-c",
    `ps -eo stat=,comm= 2>/dev/null | awk '$2=="bun" && $1 !~ /Z/ {n++} END{print n+0}'`,
  ])
    .then((r) => Number(r.stdout.trim()) || 0)
    .catch(() => 0);
  if (liveWorkers > 0) return false;
  // Catch-up clamp: a relaunched worker opens a FRESH CLI session and would
  // otherwise replay inbound from seq 0 — re-running already-finished prompts.
  // Raise the floor to the last COMPLETED turn's inbound, so processed work
  // isn't redone but anything the user sent while the worker was down (pending,
  // after the last result) is still delivered and answered.
  setInboundFloorSeq(store, sessionId, resumeFloorSeq(store, sessionId));
  // Fence any stale worker + refresh the epoch hint baked into the env.
  const epoch = bumpWorkerEpoch(store, sessionId);
  const env = { ...spec.env, CLAUDE_CODE_WORKER_EPOCH: String(epoch) };
  const envArgs = Object.entries(env).flatMap(([k, v]) => ["-e", `${k}=${v}`]);
  await exec([
    "exec",
    "-d",
    "-w",
    spec.workdir,
    ...envArgs,
    name,
    "sh",
    "-c",
    spec.cmd,
  ]);
  emit(store, sessionId, "◌ Reconnected the agent to this session");
  return true;
}

export type ContainerDiff = {
  /** Current branch in the container, e.g. jarvis/<topic> or main. */
  branch: string;
  /** Base the diff is computed against (the remote default branch). */
  base: string;
  /** Commits the branch is ahead of base. */
  ahead: number;
  /** `git diff --stat` summary text. */
  stat: string;
  /** Unified diff text (all session changes vs base, incl. new files). */
  diff: string;
};

/**
 * Read what the agent changed in a container session — the claude.ai/code
 * "review the diff" view. Diffs the working tree (committed-on-branch +
 * staged + unstaged, and new files via intent-to-add) against the remote
 * default branch, which stays pinned at the clone point, so it captures the
 * whole session regardless of whether the agent committed yet. Read-only
 * except a benign `add -N` (intent-to-add) so untracked files appear.
 */
export async function getContainerDiff(
  store: Store,
  sessionId: string,
  exec: DockerExec = realDockerExec,
  summaryOnly = false,
): Promise<ContainerDiff | { error: string }> {
  const session = findSession(store, sessionId);
  const meta = session?.container_json
    ? (JSON.parse(session.container_json) as { container?: string; repo?: string })
    : null;
  if (!meta?.container || !meta.repo) return { error: "no container" };
  const workdir = `/workspace/${repoDirName(meta.repo)}`;
  // summaryOnly skips the (potentially huge) full diff — just branch/ahead/stat,
  // for the cheap header +/- indicator that polls frequently.
  const script = [
    `cd ${workdir} 2>/dev/null || exit 0`,
    `base=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null || echo origin/main)`,
    `git add -A -N >/dev/null 2>&1`,
    `printf '@@BRANCH@@%s\\n' "$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"`,
    `printf '@@BASE@@%s\\n' "$base"`,
    `printf '@@AHEAD@@%s\\n' "$(git rev-list --count "$base"..HEAD 2>/dev/null || echo 0)"`,
    `printf '@@STAT@@\\n'`,
    `git --no-pager diff --stat "$base" 2>/dev/null`,
    `printf '@@DIFF@@\\n'`,
    ...(summaryOnly ? [] : [`git --no-pager diff "$base" 2>/dev/null`]),
  ].join("; ");
  let out: string;
  try {
    out = (await exec(["exec", meta.container, "sh", "-c", script])).stdout;
  } catch (e) {
    return { error: e instanceof Error ? e.message : String(e) };
  }
  const grab = (re: RegExp) => re.exec(out)?.[1]?.trim() ?? "";
  const statStart = out.indexOf("@@STAT@@");
  const diffStart = out.indexOf("@@DIFF@@");
  const stat =
    statStart >= 0 && diffStart >= 0
      ? out.slice(statStart + "@@STAT@@".length, diffStart).trim()
      : "";
  const diff = diffStart >= 0 ? out.slice(diffStart + "@@DIFF@@".length).replace(/^\n/, "") : "";
  return {
    branch: grab(/@@BRANCH@@(.*)/),
    base: grab(/@@BASE@@(.*)/),
    ahead: Number(grab(/@@AHEAD@@(.*)/)) || 0,
    stat,
    diff,
  };
}

/**
 * Open (or find) a pull request for the session's work — the claude.ai/code
 * "Create PR" action. Idempotent + tolerant of however far the agent already
 * got: moves off the default branch if needed, commits any pending changes,
 * pushes, then (mode `full`/`draft`) reuses an existing PR or creates one with
 * `gh pr create --fill [--draft]`, or (mode `compose`) returns GitHub's new-PR
 * compose URL for the pushed branch without opening a PR. Falls back to the
 * compare URL if `gh` is unavailable (e.g. the image predates the gh install).
 */
export async function createContainerPR(
  store: Store,
  sessionId: string,
  exec: DockerExec = realDockerExec,
  mode: "full" | "draft" | "compose" = "full",
): Promise<{ url: string; branch: string } | { error: string }> {
  const session = findSession(store, sessionId);
  const meta = session?.container_json
    ? (JSON.parse(session.container_json) as { container?: string; repo?: string })
    : null;
  if (!meta?.container || !meta.repo) return { error: "This session has no container." };
  const workdir = `/workspace/${repoDirName(meta.repo)}`;
  const branch = `jarvis/session-${sessionId.slice(0, 8)}`;
  const msg = "Changes from a Jarvis /code session";
  // PR step, by mode. `compose` skips gh and just hands back the new-PR URL.
  const prLines =
    mode === "compose"
      ? [
          `repo=$(git config --get remote.origin.url | sed -E 's#.*github.com[:/]##; s#\\.git$##')`,
          `url="https://github.com/$repo/compare/$base...$cur?expand=1"`,
        ]
      : [
          // Reuse an existing PR for this branch, else create one (draft if asked).
          `url=$(gh pr view "$cur" --json url -q .url 2>/dev/null)`,
          `if [ -z "$url" ]; then url=$(gh pr create --fill ${mode === "draft" ? "--draft " : ""}--base "$base" 2>/dev/null | grep -oE 'https://[^[:space:]]+' | head -1); fi`,
          // Fallback when gh is missing: a compare/new-PR URL the user can click.
          `if [ -z "$url" ]; then repo=$(git config --get remote.origin.url | sed -E 's#.*github.com[:/]##; s#\\.git$##'); url="https://github.com/$repo/compare/$base...$cur?expand=1"; fi`,
        ];
  const script = [
    `cd ${workdir} || exit 1`,
    `base=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##'); [ -z "$base" ] && base=main`,
    `cur=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)`,
    // On the base branch (or detached) → cut a session branch to PR from.
    `if [ "$cur" = "$base" ] || [ -z "$cur" ] || [ "$cur" = "HEAD" ]; then git checkout -b ${shq(branch)} 2>/dev/null || git checkout ${shq(branch)} 2>/dev/null; cur=$(git rev-parse --abbrev-ref HEAD 2>/dev/null); fi`,
    // Commit anything pending so the branch reflects all the work.
    `if [ -n "$(git status --porcelain)" ]; then git add -A && git commit -m ${shq(msg)} >/dev/null 2>&1; fi`,
    `git push -u origin "$cur" >/dev/null 2>&1`,
    ...prLines,
    `printf '@@PRURL@@%s\\n' "$url"`,
    `printf '@@BRANCH@@%s\\n' "$cur"`,
  ].join("\n");
  let out: string;
  try {
    out = (await exec(["exec", meta.container, "sh", "-c", script])).stdout;
  } catch (e) {
    return { error: e instanceof Error ? e.message : String(e) };
  }
  const url = /@@PRURL@@(.*)/.exec(out)?.[1]?.trim() ?? "";
  const br = /@@BRANCH@@(.*)/.exec(out)?.[1]?.trim() || branch;
  if (!url) return { error: "Could not create or find a pull request." };
  return { url, branch: br };
}

/**
 * Merge the session's PR (claude.ai/code Auto-merge). Squash-merges the PR for
 * the container's current branch via `gh pr merge`. Fails (non-fatal) when the
 * PR is missing, checks are pending, or branch protection blocks it.
 */
export async function mergeContainerPR(
  store: Store,
  sessionId: string,
  exec: DockerExec = realDockerExec,
): Promise<{ merged: true } | { error: string }> {
  const session = findSession(store, sessionId);
  const meta = session?.container_json
    ? (JSON.parse(session.container_json) as { container?: string; repo?: string })
    : null;
  if (!meta?.container || !meta.repo) return { error: "This session has no container." };
  const workdir = `/workspace/${repoDirName(meta.repo)}`;
  const script = [
    `cd ${workdir} || exit 1`,
    `cur=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)`,
    `if gh pr merge "$cur" --squash >/dev/null 2>&1; then echo @@MERGED@@1; else echo @@MERGED@@0; fi`,
  ].join("\n");
  let out: string;
  try {
    out = (await exec(["exec", meta.container, "sh", "-c", script])).stdout;
  } catch (e) {
    return { error: e instanceof Error ? e.message : String(e) };
  }
  return /@@MERGED@@1/.test(out)
    ? { merged: true }
    : { error: "Merge not allowed (checks pending or branch protected)." };
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
  // Reap the egress proxy + private network too (best-effort; no-ops for
  // `full`/non-isolated sessions that never created them).
  await exec(["rm", "-f", `jarvis-egress-${sessionId}`]).catch(() => {});
  await exec(["network", "rm", `jarvis-net-${sessionId}`]).catch(() => {});
}

/**
 * Sweep "orphaned" /code containers: ones still RUNNING under our label whose
 * session the DB no longer actively tracks — a deleted session, an archived
 * one, or a `container_json` that was cleared without the container actually
 * being removed (a failed `docker rm`). The DB-driven `runReclaimTick` only
 * looks at sessions it still tracks, so these never get reaped and pile up
 * (observed live: 5 containers up 46h+). We map each container back to its
 * session via the `jarvis-code-<sessionId>` name and reap the untracked ones.
 *
 * Safety: a freshly launched session writes `container_json` within ~seconds
 * of `docker run`, but to avoid racing that window we skip containers younger
 * than `minAgeMs`. Returns the number reaped.
 */
export async function runOrphanContainerSweep(
  store: Store,
  exec: DockerExec = realDockerExec,
  minAgeMs = 5 * 60 * 1000,
): Promise<number> {
  let names: string[];
  try {
    const { stdout } = await exec([
      "ps",
      "--filter",
      `label=${CONTAINER_LABEL}`,
      "--format",
      "{{.Names}}",
    ]);
    names = stdout
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
  } catch {
    return 0; // docker unavailable — nothing to sweep
  }
  const prefix = "jarvis-code-";
  let reaped = 0;
  for (const name of names) {
    if (!name.startsWith(prefix)) continue;
    const sessionId = name.slice(prefix.length);
    if (!sessionId) continue;
    // Still tracked by a live, non-archived session → leave it to the
    // DB-driven idle reclaim, which respects last-activity.
    const session = findSession(store, sessionId);
    if (session && session.container_json && !session.archived) continue;
    // Don't reap a container that may still be mid-launch (container_json not
    // yet written). StartedAt is ISO-8601; an unparseable/missing value (the
    // container vanished under us) falls through to the reap attempt.
    try {
      const { stdout } = await exec(["inspect", "-f", "{{.State.StartedAt}}", name]);
      const startedAt = Date.parse(stdout.trim());
      if (Number.isFinite(startedAt) && Date.now() - startedAt < minAgeMs) continue;
    } catch {
      /* inspect failed — treat as reapable */
    }
    await stopContainerSession(store, sessionId, exec);
    if (session) clearSessionContainer(store, sessionId);
    reaped++;
  }
  return reaped;
}
