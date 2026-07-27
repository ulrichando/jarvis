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
  getDiffSnapshot,
  getWorkerSpec,
  parseEnvironmentConfig,
  resumeFloorSeq,
  setDiffSnapshot,
  setInboundFloorSeq,
  setSessionContainer,
  setSessionToken,
  setWorkerSpec,
  type Store,
} from "./store";
import { freshInstallationToken } from "./gh-app-token";
import { githubStatus } from "../connectors/github";
import { MODELS_META } from "../ai/models-meta";
import { listMcpServers } from "../mcp/store";
import { signProxyToken } from "./proxyJwt";

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
/** Domains a `trusted`/`custom` egress level always allows (package registries).
 *  GitHub git is reached ONLY through the host-side scoped-credential proxy
 *  (host.docker.internal, in NO_PROXY), so github.com is intentionally absent. */
export const DEFAULT_ALLOW = [
  ".githubusercontent.com",
  ".npmjs.org",
  "pypi.org",
  "files.pythonhosted.org",
  ".crates.io",
  ".rubygems.org",
  ".debian.org",
  ".ubuntu.com",
  "host.docker.internal",
];

/** Drop any domain already covered by a `.wildcard` in the same list. squid
 *  FATALs ("Bungled squid.conf … is a subdomain of …") and REFUSES TO START if
 *  a dstdomain ACL contains both `.npmjs.org` and a subdomain like
 *  `registry.npmjs.org` — which silently kills the whole isolated egress level.
 *  Defends DEFAULT_ALLOW + any user customAllowlist against that class. */
export function dedupeSquidDomains(domains: string[]): string[] {
  const wildcards = domains.filter((d) => d.startsWith("."));
  return domains.filter((d) => {
    const bare = d.startsWith(".") ? d.slice(1) : d;
    return !wildcards.some((w) => w !== d && (bare === w.slice(1) || bare.endsWith(w)));
  });
}

/** Generate a squid forward-proxy config that allows CONNECT/HTTP only to the
 *  given domains and denies everything else (empty list = deny all). */
export function buildSquidConf(domains: string[]): string {
  const safe = dedupeSquidDomains(domains);
  const acls = safe.map((d) => `acl allowed dstdomain ${d}`).join("\n");
  return [
    "http_port 3128",
    acls,
    safe.length ? "http_access allow allowed" : "",
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

/**
 * Best-effort teardown of a session's whole docker footprint: the workbench
 * container, its egress proxy, and its private network — container FIRST
 * (docker refuses to remove a network with an attached container). A launch
 * that fails AFTER the egress proxy + network are created must call this or
 * they leak until the next orphan sweep (finding #9). No-ops for `full` /
 * non-isolated sessions that never created the egress/network.
 */
export async function teardownSessionDocker(
  exec: DockerExec,
  sessionId: string,
  containerName?: string,
): Promise<void> {
  await exec(["rm", "-f", containerName ?? containerNameFor(sessionId)]).catch(
    () => {},
  );
  await exec(["rm", "-f", `jarvis-egress-${sessionId}`]).catch(() => {});
  await exec(["network", "rm", `jarvis-net-${sessionId}`]).catch(() => {});
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
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo)) return false;
  // A charset-valid segment that is nothing but dots (`.` / `..`) is path
  // traversal, not a repo name — aligned with git-proxy.ts::validName.
  return repo.split("/").every((seg) => !/^\.+$/.test(seg));
}

/**
 * POSIX single-quote a value for safe embedding in a `sh -c` command. The
 * GitHub token / login flow through this when we configure git credentials
 * in-container, so a stray quote can't break out of the command.
 */
function shq(s: string): string {
  return `'${s.replace(/'/g, `'\\''`)}'`;
}

/** In-container path of the static git credential helper script. Lives in
 *  /jarvis-config (created early, writable by the exec user) so it works
 *  whether or not the image user can write /usr/local/bin. */
const GIT_CRED_HELPER_PATH = "/jarvis-config/git-cred";

/**
 * Sh command installing a STATIC git credential helper: a script that always
 * answers `get` with the session's cap token. Deliberately NOT
 * `credential.helper store` + ~/.git-credentials: on any 401 from the git
 * proxy (cap-token remint on relaunch, a transient auth failure) git calls the
 * store helper's `erase`, which deletes the credential line — after that every
 * push finds no credential and git tries to PROMPT, wedging the session with
 * "could not read Username for 'http://web:3000'". A helper program has no
 * erase, so a rejected request can never evict the credential. (Claude Code's
 * sandbox git proxy ships a helper binary rather than a cred file for the same
 * reason.) The helper answers for every host — harmless: the token is
 * per-session and only the proxy accepts it.
 */
function gitCredHelperCmd(gitCapToken: string, workdir?: string): string {
  const lines = [
    "#!/bin/sh",
    '[ "$1" = get ] || exit 0',
    `printf 'username=%s\\npassword=%s\\n' x-access-token '${gitCapToken}'`,
  ];
  return [
    "mkdir -p /jarvis-config",
    `printf '%s\\n' ${lines.map((l) => shq(l)).join(" ")} > ${GIT_CRED_HELPER_PATH}`,
    `chmod 700 ${GIT_CRED_HELPER_PATH}`,
    // Unset-all BEFORE set: with multiple existing values (old baked images,
    // agent-added helpers) a plain `git config` set fails. Also drop any
    // repo-local helper a wedged agent hand-rolled, and the store-file remnant.
    "{ git config --global --unset-all credential.helper 2>/dev/null || true; }",
    ...(workdir
      ? [`{ git -C ${shq(workdir)} config --unset-all credential.helper 2>/dev/null || true; }`]
      : []),
    `git config --global credential.helper ${GIT_CRED_HELPER_PATH}`,
    `rm -f "$HOME/.git-credentials"`,
  ].join(" && ");
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
    /** http://host:port — the PUBLIC origin the browser session URL is built
     *  from (JARVIS_SESSION_URL). Also the child's callback/git-proxy origin
     *  UNLESS internalBaseUrl is set (see below). */
    baseUrl: string;
    /** Container-facing origin for the git-proxy + CCR callback, for deploys
     *  where this app is NOT reachable via host.docker.internal (containerized
     *  behind Cloudflare: web is expose-only, the model proxy binds loopback).
     *  When set (isolated only), the workbench also joins the shared
     *  `jarvis-code-bridge` network so this origin's host (e.g. http://web:3000)
     *  resolves by service name, while `baseUrl` stays PUBLIC for the session
     *  URL — the two diverge on such deploys. Unset (local/desktop): the child
     *  reaches this app via the host.docker.internal rewrite of baseUrl. */
    internalBaseUrl?: string;
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
    /** EXTERNAL bot job (gh-app dispatch): a repo-scoped GitHub App
     *  installation token (~1h). Persisted into the session container meta so
     *  the scoped git proxy + host-side PR path authenticate with it. It is
     *  NEVER placed in the container (env or argv) — the container only ever
     *  holds the per-session cap token. Absent for normal /code sessions,
     *  which then behave byte-identically to before this field existed. */
    installationToken?: string;
    /** The App bot's login — the committer identity for external bot jobs. */
    botLogin?: string;
    /** Host path of an uploaded git seed bundle (bundle-mode teleport from a
     *  local-only repo — lib/bridge/bundles.ts). Bind-mounted read-only at
     *  /jarvis-seed.bundle and `git clone`d into the workdir instead of a
     *  git-proxy clone (repoFullName is '' in this mode). MUST resolve on the
     *  HOST: the docker daemon is the host's, so only paths under a
     *  passthrough mount (the workspaces root) work on the containerized
     *  deploy. */
    bundlePath?: string;
  },
): Promise<void> {
  const exec = opts.exec ?? realDockerExec;
  const { sessionId, repoFullName } = opts;
  const name = containerNameFor(sessionId);
  // No-repo sessions (ultraplan / research / remote agentic tasks): the CCR
  // path launches a container that just runs the agent, no repo to clone.
  // repoDirName("") → "repo", so the workdir stays a stable /workspace/repo.
  const hasRepo = validRepoFullName(repoFullName);
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
  // Per-session git capability token — the ONLY git credential the container
  // holds. The real PAT is injected host-side by the git proxy route.
  const { randomBytes: gitRand } = await import("node:crypto");
  const gitCapToken = `git_${gitRand(24).toString("base64url")}`;

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
      // Full teardown, not just the container — a mid-launch failure after the
      // egress proxy + private network were created would otherwise leak them
      // until the next orphan sweep (finding #9).
      await teardownSessionDocker(exec, sessionId, name);
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
  // Container-facing origin for the git-proxy + CCR callback. A caller (the
  // gh-app dispatch) may pass it explicitly; otherwise it comes from the deploy
  // env (JARVIS_CODE_INTERNAL_ORIGIN=http://web:3000 on the containerized VPS),
  // so EVERY container session — not just bot jobs — reaches this app
  // internally. Unset (local/desktop) → the host.docker.internal path below.
  const internalBaseUrl = opts.internalBaseUrl ?? process.env.JARVIS_CODE_INTERNAL_ORIGIN;
  // A configured internal origin FORCES the isolated bridge path: the container
  // must join jarvis-code-bridge to reach web:3000, and a host-network (`full`)
  // container can't (web is expose-only, not host-published). Without one, the
  // env's own networkLevel decides.
  const isolated = netLevel !== "full" || !!internalBaseUrl;
  const netName = `jarvis-net-${sessionId}`;
  const proxyName = `jarvis-egress-${sessionId}`;
  const netArgs = isolated
    ? ["--network", netName, "--add-host=host.docker.internal:host-gateway"]
    : ["--network=host"];
  // Containerized deploy (host.docker.internal unreachable): the child reaches
  // this app + the model proxy over a shared bridge, naming them by service.
  const internalMode = isolated && !!internalBaseUrl;
  const CODE_BRIDGE_NET = process.env.JARVIS_CODE_BRIDGE_NETWORK || "jarvis-code-bridge";
  // The child's callback/git-proxy origin. internalMode → the service-name
  // origin (e.g. http://web:3000) reached over CODE_BRIDGE_NET; else isolated →
  // swap 127.0.0.1 for the host-gateway alias; else (host net) → as-is.
  const childBaseUrl = internalMode
    ? internalBaseUrl!.replace(/\/+$/, "")
    : isolated
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
      // Seed bundle (bundle-mode teleport): mount the uploaded bundle file
      // read-only; the clone step below seeds the workdir from it.
      ...(opts.bundlePath ? ["-v", `${opts.bundlePath}:/jarvis-seed.bundle:ro`] : []),
      runImage,
      "sleep",
      "infinity",
    ]);
    // Containerized deploy: join the shared internal bridge so the child
    // resolves this app + the model proxy by service name. internal:true — only
    // web+hub live there, so egress stays squid-only and postgres/docker-proxy
    // stay unreachable. Best-effort: a miss surfaces at the clone step below.
    if (internalMode) {
      await exec(["network", "connect", CODE_BRIDGE_NET, name]).catch((err) => {
        const msg = err instanceof Error ? err.message : String(err);
        emit(store, sessionId, `⚠ shared bridge (${CODE_BRIDGE_NET}) attach failed — ${msg.slice(0, 160)}`);
      });
    }
    setSessionContainer(store, sessionId, {
      container: name,
      repo: repoFullName,
      extraRepos,
      gitCapToken,
      // External bot jobs only — the spread keeps the persisted JSON
      // byte-identical for normal sessions (no extra keys).
      ...(opts.installationToken ? { installationToken: opts.installationToken } : {}),
      ...(opts.botLogin ? { botLogin: opts.botLogin } : {}),
    });
    if (cacheHit) emit(store, sessionId, "◌ Restored cached environment (setup skipped)");
  });

  // 2. Cloned repository — git is push-capable WITHOUT the real token ever
  // entering the container: it talks to the host-side scoped-credential git
  // proxy (this app) with a per-session cap token; the proxy injects the real
  // PAT. See app/api/bridge/v1/code/sessions/[sessionId]/git/[...path] + the
  // design spec (2026-06-19-scoped-credential-git-proxy-design.md).
  const gh = await githubStatus();

  // The proxy base the in-container git talks to (same host:port the child uses
  // for callbacks); owner/repo is appended per-remote.
  const proxyOrigin = childBaseUrl.replace(/\/+$/, "");
  const proxyRemote = (full: string): string =>
    `${proxyOrigin}/api/bridge/v1/code/sessions/${sessionId}/git/${full}.git`;
  // Committer identity + the cap-token credential HELPER so `git commit`/
  // `push` work non-interactively through the proxy (see gitCredHelperCmd for
  // why a helper script and not `credential.helper store`). Runs on EVERY
  // launch (cache restores scrub creds; cap tokens are per-session). Must run
  // BEFORE clone/fetch so the credential helper can authorize them. Non-fatal:
  // a hiccup warns rather than aborting the launch.
  const configureGitProxy = async (): Promise<void> => {
    // External bot jobs commit as the App bot, not the connected user.
    const login =
      opts.installationToken && opts.botLogin ? opts.botLogin : gh.login || "jarvis";
    const email = `${login}@users.noreply.github.com`;
    const cmd = [
      `git config --global user.name ${shq(login)}`,
      `git config --global user.email ${shq(email)}`,
      gitCredHelperCmd(gitCapToken),
      `git config --global init.defaultBranch main`,
      `git config --global --add safe.directory ${shq(workdir)}`,
    ].join(" && ");
    try {
      await exec(["exec", name, "sh", "-c", cmd]);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      emit(store, sessionId, `⚠ git proxy credentials not configured — ${msg.slice(0, 200)}`);
    }
  };

  // 2. Cloned repository (or, on a cache hit, freshen the baked-in checkout).
  // All git goes through the per-session proxy URL — no token in any argv.
  await step(
    opts.bundlePath
      ? "Seeded from bundle"
      : !hasRepo ? "Prepared workspace" : cacheHit ? "Restored repository" : "Cloned repository",
    async () => {
    // Write the cap credential FIRST so clone/fetch through the proxy can auth.
    await configureGitProxy();
    if (opts.bundlePath) {
      // Bundle-mode seed (local-only repo): clone the bind-mounted bundle.
      // There is no origin remote a push could target — outcomes stay
      // in-container.
      //
      // Squashed-tier bundles carry ONLY refs/seed/root (no refs/heads/*, no
      // HEAD), so a plain `git clone` exits 0 with "remote HEAD refers to
      // nonexistent ref" and leaves an EMPTY worktree. Fallback: fetch
      // refs/seed/root and check it out detached. Then VERIFY HEAD resolves —
      // if the workspace is still empty, exit non-zero with the message on
      // stderr so the step machinery emits the ✗ event and tears down like a
      // failed clone (never a lying ✓ "Seeded from bundle" over nothing).
      //
      // WIP overlay: the CLI packs uncommitted (tracked) changes as a
      // stash-format commit at refs/seed/stash (gitBundle.ts: `git stash
      // create` → update-ref → bundle). After a clean clone the base HEAD
      // matches, so `git stash apply` reconstitutes them as working-tree edits —
      // the container plans against the caller's actual in-progress state, not
      // just the last commit. Best-effort: absent on no-WIP repos and
      // squashed-tier bundles (fetch fails → no-op; the squashed tier already
      // bakes WIP into refs/seed/root), and a failed apply never fails the seed
      // (committed history is the guarantee, WIP is a bonus). Untracked/new
      // files are NOT captured — `git stash create` semantics, matches upstream.
      // Single sh -c so clone + fallback + verify share one exit status.
      const seedCmd = [
        `git clone /jarvis-seed.bundle ${shq(workdir)} || true`,
        `if ! git -C ${shq(workdir)} rev-parse --verify -q HEAD >/dev/null 2>&1; then`,
        `  git init -q ${shq(workdir)} 2>/dev/null || true`,
        `  git -C ${shq(workdir)} fetch -q /jarvis-seed.bundle 'refs/seed/root' && git -C ${shq(workdir)} checkout -q FETCH_HEAD`,
        `fi`,
        `git -C ${shq(workdir)} rev-parse --verify -q HEAD >/dev/null 2>&1 || { echo 'bundle seed produced an empty workspace' >&2; exit 1; }`,
        // Best-effort WIP overlay (see comment above). The `|| true` keeps this
        // from ever flipping the seed's exit code — committed history already
        // verified above; the stash is a bonus.
        `git -C ${shq(workdir)} fetch -q /jarvis-seed.bundle 'refs/seed/stash' 2>/dev/null && git -C ${shq(workdir)} stash apply -q FETCH_HEAD 2>/dev/null || true`,
      ].join("\n");
      await exec(["exec", name, "sh", "-c", seedCmd]);
      return;
    }
    if (!hasRepo) {
      // No repo to clone. Create an empty git workdir so the CLI's workspace
      // trust check + any git-dependent tooling still work (the agent just
      // reasons/plans; nothing is pushed).
      await exec([
        "exec", name, "sh", "-c",
        `mkdir -p ${shq(workdir)} && git -C ${shq(workdir)} init -q 2>/dev/null || true`,
      ]);
      return;
    }
    if (cacheHit) {
      // The baked-in remote points at a PREVIOUS session's proxy path — reset it
      // to THIS session's proxy URL before fetching, then freshen the checkout.
      await exec([
        "exec", "-w", workdir, name, "git", "remote", "set-url", "origin", proxyRemote(repoFullName),
      ]).catch(() => {});
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
      // Clone via the proxy URL; the remote stays the proxy URL (auth via the
      // credential helper). No token is embedded, so no set-url scrub is needed.
      await exec(["exec", name, "git", "clone", proxyRemote(repoFullName), workdir]);
    }
    // Multi-repo: clone each additional repo via the proxy too. The session's
    // git scope (set at container setup) must include these for pushes to pass.
    for (const extra of extraRepos) {
      const edir = `/workspace/${repoDirName(extra)}`;
      await exec(["exec", name, "git", "clone", proxyRemote(extra), edir]).catch(() => {});
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
        await exec([
          "exec", name, "sh", "-c",
          `rm -f "$HOME/.git-credentials" ${GIT_CRED_HELPER_PATH}`,
        ]).catch(() => {});
        await exec(["commit", name, cacheTag]);
        await configureGitProxy();
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
    // The jarvis CLI reaches every provider (DeepSeek/OpenAI/Gemini AND
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
    // internalMode reaches the proxy by its compose service name over the shared
    // bridge (set JARVIS_CLI_PROXY_URL=http://hub:4000), so no host-gateway swap;
    // plain isolated swaps 127.0.0.1 for the host-gateway alias; host net → as-is.
    const proxyHealthUrl = process.env.JARVIS_CLI_PROXY_URL || "http://127.0.0.1:4000";
    const proxyUrl = internalMode
      ? proxyHealthUrl
      : isolated
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
      // When the proxy enforces auth (JARVIS_PROXY_AUTH_REQUIRED=1 — the
      // containerized deploy), the CLI must present a signed proxy JWT: the same
      // credential `jarvis auth login` mints, carried as ANTHROPIC_AUTH_TOKEN
      // (→ Authorization: Bearer). The headless workbench can't run login, so
      // mint one here (session-scoped, 24h) with the shared JARVIS_PROXY_JWT_SECRET
      // the hub verifies against. Read the env secret DIRECTLY (never
      // getOrCreate — a fresh secret wouldn't match the hub, and writing one is a
      // side effect); when it's absent (auth-less local proxy) the placeholder
      // key already suffices, so skip.
      const proxyJwtSecret = process.env.JARVIS_PROXY_JWT_SECRET?.trim();
      if (proxyJwtSecret) {
        routingEnv.ANTHROPIC_AUTH_TOKEN = signProxyToken(
          { sub: `code-session:${sessionId}`, ttlSeconds: 60 * 60 * 24 },
          proxyJwtSecret,
        );
      }
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

    // NO_PROXY: internalMode reaches web + the model proxy directly over the
    // shared bridge (not via squid), so name their service hosts; else only the
    // host-gateway alias is a direct hop.
    const hostOf = (u: string): string => {
      try {
        return new URL(u).hostname;
      } catch {
        return "";
      }
    };
    const noProxyHosts = internalMode
      ? Array.from(
          new Set(
            [
              hostOf(internalBaseUrl!),
              hostOf(proxyHealthUrl),
              "localhost",
              "127.0.0.1",
            ].filter(Boolean),
          ),
        ).join(",")
      : "host.docker.internal,localhost,127.0.0.1";

    const childEnv: Record<string, string> = {
      // User-configured env vars first, so the worker-handshake + routing keys
      // below always win over anything the user set with the same name.
      ...envConfig.envVars,
      CLAUDE_CONFIG_DIR: "/jarvis-config",
      CLAUDE_CODE_SESSION_ACCESS_TOKEN: token!,
      CLAUDE_CODE_USE_CCR_V2: "1",
      CLAUDE_CODE_WORKER_EPOCH: String(epoch),
      CLAUDE_CODE_ENVIRONMENT_KIND: "bridge",
      // The workbench CLI is headless — it authenticates via the local model
      // proxy + the per-session worker token, never an interactive account
      // login. Skip the `jarvis auth login` gate (which otherwise exits the CLI
      // immediately: "Authentication required"), the same way the gh-app sandbox
      // path does. JARVIS_DISABLE_AUTH (routingEnv) is the proxy-side switch; this
      // is the CLI-startup login gate — a distinct flag.
      JARVIS_REQUIRE_LOGIN: "0",
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
        NO_PROXY: noProxyHosts,
        no_proxy: noProxyHosts,
      }),
      // NO GH_TOKEN/GITHUB_TOKEN: the real GitHub credential never enters the
      // container. git auths to the host-side proxy via the cap-token credential
      // helper; PR open/merge are host-side REST actions (createContainerPR).
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
    // The clone/commit/push guidance below is TRUE only for a cloned-repo
    // session. A no-repo scratch session (ultraplan / from-scratch / research)
    // is an empty `git init` workspace with no remote — telling it "this is a
    // clone of the repository... push a branch" made the agent hunt for a repo,
    // find nothing, and reply "the repository is empty, did you mean a different
    // repository" instead of just planning/building. Branch the guidance on
    // hasRepo. (No apostrophes: this string is single-quoted in the sh -c below.)
    const identityPrompt =
      "Your name is Jarvis. Never refer to yourself as Claude, Claude Code, or an Anthropic CLI in user-facing replies; introduce yourself and sign off as Jarvis. " +
      (hasRepo
        ? "This workspace is a clone of the selected GitHub repository and git is fully configured here: user.name and user.email are already set, and a credential helper supplies the GitHub push token, so git commit and git push both work without any prompting. " +
          "Never ask for a git name, email, or credentials, and never claim you are unable to commit or push. " +
          "When you make code changes worth keeping, save them with git proactively: create a branch named jarvis/<short-topic>, stage the changes, commit with a clear concise message, and run git push -u origin <branch>. " +
          "Git here is wired through a secure proxy that authorizes pushes to this session repository, so git push works without prompting. Do not run the gh CLI or call the GitHub API directly: opening the pull request is a host action available from the session panel. After you push a branch, tell the user it is pushed and that the pull request can be opened from the panel. " +
          "Do all of this automatically whenever you finish a unit of work or the user asks you to save, commit, merge, push, or open a PR; never reply that you were not asked to. "
        : "This is a fresh from-scratch workspace: an empty git-initialized directory with NO cloned repository and NO git remote. Do not look for existing project files, do not treat the empty workspace as a problem, and never tell the user the repository is empty or ask them to pick a different repository. Just carry out the request directly here: for a plan-mode session, research the request and produce the plan; otherwise build what was asked from scratch in this directory. There is no remote to push to, so do not run git push or open a pull request; the work stays in this container. ") +
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
  let gitCapToken: string | undefined;
  try {
    const meta = JSON.parse(session.container_json) as {
      container?: string;
      gitCapToken?: string;
    };
    name = meta.container;
    gitCapToken = meta.gitCapToken;
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
  // Re-assert the static credential helper on every reopen (best-effort,
  // idempotent). Heals sessions wedged by the old erasable store-file
  // credential — a single proxy 401 evicted it and every push after failed
  // with "could not read Username" — without waiting for a new launch.
  if (gitCapToken) {
    await exec([
      "exec", name, "sh", "-c", gitCredHelperCmd(gitCapToken, spec.workdir),
    ]).catch(() => {});
  }
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
  /** True when this is the last CAPTURED diff, served because the live
   *  container state is gone (reclaimed/stopped) or empty. */
  stale?: boolean;
};

/** Cap a stored diff body so a giant patch can't bloat the sessions row.
 *  ponytail: flat cap; per-file trimming if a real session ever hits it. */
const DIFF_SNAPSHOT_MAX = 500_000;
function capDiff(diff: string): string {
  return diff.length > DIFF_SNAPSHOT_MAX
    ? diff.slice(0, DIFF_SNAPSHOT_MAX) + "\n… (diff truncated for storage)"
    : diff;
}

/**
 * Read the CLI's NATIVE session transcript (jsonl) out of the container, for
 * full-fidelity teleport: the worker runs with --session-id <sessionId> and
 * CLAUDE_CONFIG_DIR=/jarvis-config, so its own transcript lives at
 * /jarvis-config/projects/<cwd-slug>/<sessionId>.jsonl. Dropping that file
 * into the LOCAL project dir lets `jarvis --resume <sessionId>` continue the
 * exact conversation (claude.ai --teleport behavior), not a summary of it.
 * Returns null when the container is gone or the file is missing/oversized —
 * callers fall back to the markdown transcript.
 */
const CLI_TRANSCRIPT_MAX_BYTES = 20_000_000;
export async function readCliTranscript(
  store: Store,
  sessionId: string,
  exec: DockerExec = realDockerExec,
): Promise<string | null> {
  // sessionId reaches a `sh -c` string below; ids are server-minted hex, but
  // this is called with a URL path param — reject anything shell-meaningful.
  if (!/^[A-Za-z0-9_-]+$/.test(sessionId)) return null;
  const session = findSession(store, sessionId);
  const meta = session?.container_json
    ? (JSON.parse(session.container_json) as { container?: string })
    : null;
  if (!meta?.container) return null;
  try {
    const { stdout } = await exec([
      "exec", meta.container, "sh", "-c",
      `cat /jarvis-config/projects/*/${sessionId}.jsonl 2>/dev/null`,
    ]);
    if (!stdout.trim() || stdout.length > CLI_TRANSCRIPT_MAX_BYTES) return null;
    return stdout;
  } catch {
    return null;
  }
}

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
  // The last captured diff, served (marked stale) whenever live state is
  // unavailable or empty — the container is ephemeral (idle reclaim,
  // redeploys, an agent fetch catching base up), the session's changes
  // shouldn't be. Without this the "View changes" chip + Diff panel silently
  // go blank once the container disappears.
  const snapshot = (): ContainerDiff | null => {
    const snap = getDiffSnapshot(store, sessionId);
    if (!snap) return null;
    const { branch, base, ahead, stat, diff } = snap;
    return { branch, base, ahead, stat, diff, stale: true };
  };
  if (!meta?.container || !meta.repo) return snapshot() ?? { error: "no container" };
  const workdir = `/workspace/${repoDirName(meta.repo)}`;
  // summary skips the (potentially huge) full diff — just branch/ahead/stat,
  // for the cheap header +/- indicator that polls frequently.
  const read = async (summary: boolean): Promise<ContainerDiff | { error: string }> => {
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
      ...(summary ? [] : [`git --no-pager diff "$base" 2>/dev/null`]),
    ].join("; ");
    let out: string;
    try {
      out = (await exec(["exec", meta.container!, "sh", "-c", script])).stdout;
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
  };
  const live = await read(summaryOnly);
  // Container stopped/removed, or worktree no longer differs from base →
  // fall back to the snapshot so the session's changes stay viewable.
  if ("error" in live) return snapshot() ?? live;
  if (!live.stat.trim()) return snapshot() ?? live;
  // Persist non-empty reads. Summary reads carry no diff body, so when the
  // stat actually changed take ONE full read for the snapshot (rare: only
  // when the working tree changed since the last capture).
  const prev = getDiffSnapshot(store, sessionId);
  if (summaryOnly) {
    if (prev?.stat !== live.stat) {
      const full = await read(false);
      if (!("error" in full) && full.stat.trim()) {
        setDiffSnapshot(store, sessionId, { ...full, diff: capDiff(full.diff), at: Date.now() });
      }
    }
  } else {
    const capped = capDiff(live.diff);
    if (prev?.stat !== live.stat || prev?.diff !== capped) {
      setDiffSnapshot(store, sessionId, { ...live, diff: capped, at: Date.now() });
    }
  }
  return live;
}

/**
 * Open (or find) a pull request for the session's work — the claude.ai/code
 * "Create PR" action. Idempotent + tolerant of however far the agent already
 * got: moves off the default branch if needed, commits any pending changes,
 * pushes (through the git proxy), then opens or reuses the PR HOST-SIDE via the
 * GitHub REST API (the real token never enters the container), or (mode
 * `compose`) returns GitHub's new-PR compose URL without opening a PR. Falls
 * back to the compare URL if the REST call fails.
 */
export async function createContainerPR(
  store: Store,
  sessionId: string,
  exec: DockerExec = realDockerExec,
  mode: "full" | "draft" | "compose" = "full",
): Promise<{ url: string; branch: string } | { error: string }> {
  const session = findSession(store, sessionId);
  const meta = session?.container_json
    ? (JSON.parse(session.container_json) as {
        container?: string;
        repo?: string;
        installationToken?: string;
      })
    : null;
  if (!meta?.container || !meta.repo) return { error: "This session has no container." };
  const workdir = `/workspace/${repoDirName(meta.repo)}`;
  const branch = `jarvis/session-${sessionId.slice(0, 8)}`;
  // External bot jobs (gh-app dispatch) stamp the watchable session URL into
  // the commit + PR so a reviewer opens the exact run from GitHub (claude.ai/
  // code parity: transcript link + session trailer). The URL is the one baked
  // into the worker env at launch (JARVIS_SESSION_URL, from the dispatch's
  // explicit publicOrigin). Normal sessions have no installationToken →
  // sessionUrl stays null → message + body are byte-identical to today.
  const sessionUrl = meta.installationToken
    ? (getWorkerSpec(store, sessionId)?.env.JARVIS_SESSION_URL ?? null)
    : null;
  const msg = sessionUrl
    ? `Changes from a Jarvis /code session\n\nJarvis-Session: ${sessionUrl}`
    : "Changes from a Jarvis /code session";
  // In-container: cut a session branch if needed, commit pending work, push
  // (through the git proxy). Report base + branch back; the PR is opened
  // host-side so the container never needs a GitHub token.
  const script = [
    `cd ${workdir} || exit 1`,
    `base=$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##'); [ -z "$base" ] && base=main`,
    `cur=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)`,
    // On the base branch (or detached) → cut a session branch to PR from.
    `if [ "$cur" = "$base" ] || [ -z "$cur" ] || [ "$cur" = "HEAD" ]; then git checkout -b ${shq(branch)} 2>/dev/null || git checkout ${shq(branch)} 2>/dev/null; cur=$(git rev-parse --abbrev-ref HEAD 2>/dev/null); fi`,
    // Commit anything pending so the branch reflects all the work.
    `if [ -n "$(git status --porcelain)" ]; then git add -A && git commit -m ${shq(msg)} >/dev/null 2>&1; fi`,
    // Push with the exit code CHECKED — a silent 401 here (e.g. an expired
    // installation token on an external job) used to yield a compare URL to a
    // branch that never reached the remote.
    `if git push -u origin "$cur" >/dev/null 2>&1; then printf '@@PUSH@@ok\\n'; else printf '@@PUSH@@fail\\n'; fi`,
    `printf '@@BASE@@%s\\n' "$base"`,
    `printf '@@BRANCH@@%s\\n' "$cur"`,
  ].join("\n");
  let out: string;
  try {
    out = (await exec(["exec", meta.container, "sh", "-c", script])).stdout;
  } catch (e) {
    return { error: e instanceof Error ? e.message : String(e) };
  }
  const base = /@@BASE@@(.*)/.exec(out)?.[1]?.trim() || "main";
  const cur = /@@BRANCH@@(.*)/.exec(out)?.[1]?.trim() || branch;
  // An explicit push failure is a hard error — never hand back a compare/PR
  // URL for a branch that was not pushed. (A missing marker stays lenient for
  // back-compat with pre-marker callers/mocks; the real script always emits it.)
  if (/@@PUSH@@fail/.test(out)) {
    return {
      error:
        `git push failed for ${cur} — the branch was NOT pushed to ${meta.repo}. ` +
        (meta.installationToken
          ? "The App installation token could not be refreshed (is GH_APP_INTERNAL_URL set and the gh-app healthy?) or has expired — as a last resort, re-dispatch the job."
          : "Check the GitHub connection (Settings) and the git proxy log."),
    };
  }
  const compareUrl = `https://github.com/${meta.repo}/compare/${base}...${cur}?expand=1`;

  // `compose` just hands back GitHub's new-PR URL (no PR opened).
  if (mode === "compose") return { url: compareUrl, branch: cur };

  const { openPullRequest } = await import("../connectors/github");
  const prTitle = "Changes from a Jarvis /code session";
  // Default body kept as-is; external jobs only APPEND the session link.
  const prBody = sessionUrl
    ? `From a Jarvis /code session.\n\nSession: ${sessionUrl}\n\nJarvis-Session: ${sessionUrl}`
    : "From a Jarvis /code session.";
  // External bot jobs authenticate the PR with the injected installation
  // token (refreshed through the gh-app when stale — a late PR open can
  // outlive the ~1h dispatch token); normal sessions call exactly as before.
  const prToken = meta.installationToken
    ? await freshInstallationToken(store, sessionId, session)
    : null;
  const pr = prToken
    ? await openPullRequest(meta.repo, cur, base, prTitle, prBody, mode === "draft", prToken)
    : await openPullRequest(meta.repo, cur, base, prTitle, prBody, mode === "draft");
  // On any REST failure, fall back to a clickable compare URL.
  if (!pr.ok) return { url: compareUrl, branch: cur };
  return { url: pr.url, branch: cur };
}

/**
 * Merge the session's PR (claude.ai/code Auto-merge). Reads the container's
 * current branch, then resolves + squash-merges its PR HOST-SIDE via REST (no
 * GitHub token in the container). Fails (non-fatal) when the PR is missing,
 * checks are pending, or branch protection blocks it.
 */
export async function mergeContainerPR(
  store: Store,
  sessionId: string,
  exec: DockerExec = realDockerExec,
): Promise<{ merged: true } | { error: string }> {
  const session = findSession(store, sessionId);
  const meta = session?.container_json
    ? (JSON.parse(session.container_json) as {
        container?: string;
        repo?: string;
        installationToken?: string;
      })
    : null;
  if (!meta?.container || !meta.repo) return { error: "This session has no container." };
  const workdir = `/workspace/${repoDirName(meta.repo)}`;
  let cur: string;
  try {
    cur = (
      await exec(["exec", meta.container, "sh", "-c", `cd ${workdir} && git rev-parse --abbrev-ref HEAD 2>/dev/null`])
    ).stdout.trim();
  } catch (e) {
    return { error: e instanceof Error ? e.message : String(e) };
  }
  if (!cur || cur === "HEAD") return { error: "No branch to merge." };
  const { githubPrStatus, mergePullRequest } = await import("../connectors/github");
  // External bot jobs authenticate lookup + merge with the injected
  // installation token (refreshed through the gh-app when stale); normal
  // sessions call exactly as before (no extra arg).
  const mergeToken = meta.installationToken
    ? await freshInstallationToken(store, sessionId, session)
    : null;
  const status = mergeToken
    ? await githubPrStatus(meta.repo, cur, mergeToken)
    : await githubPrStatus(meta.repo, cur);
  if (!status.ok || !status.status.pr) return { error: "No open pull request for this branch." };
  const merged = mergeToken
    ? await mergePullRequest(meta.repo, status.status.pr.number, "squash", mergeToken)
    : await mergePullRequest(meta.repo, status.status.pr.number);
  return merged.ok ? { merged: true } : { error: merged.error };
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
  await teardownSessionDocker(exec, sessionId, name);
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
