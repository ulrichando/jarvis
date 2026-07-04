import { NextResponse } from "next/server";
import { randomBytes, randomUUID, timingSafeEqual } from "node:crypto";
import { getStore } from "@/lib/bridge/db";
import {
  appendInbound,
  appendSessionEvent,
  createEnvironment,
  getOrCreateSession,
  listEnvironments,
  setEnvironmentConfig,
} from "@/lib/bridge/store";
import { launchContainerSession, validRepoFullName } from "@/lib/bridge/containers";
import { bridgeError } from "@/lib/bridge/errors";
import { LOCAL_USER_ID } from "@/lib/chat/persist";

export const runtime = "nodejs";

// POST /api/bridge/v1/gh-app/dispatch — INTERNAL service endpoint: the gh-app
// worker turns an approved `@jarvis-gh-bot <task>` job into a real, watchable
// /code container session (runRoutine-shaped: env get-or-create for the
// external repo → session → seed bypassPermissions + the task → launch).
//
// AUTH IS SERVICE-TOKEN ONLY (GH_APP_BRIDGE_TOKEN in the dedicated
// `X-GH-App-Token` header, constant-time compare) — there is no browser here,
// so getUserId is deliberately NOT used. Unset env → the route is inert (every
// request 401s). The token is NOT read from `Authorization`: in prod
// (JARVIS_REQUIRE_LOCAL_AUTH=1) src/proxy.ts already claims that header for
// its network bearer gate (`Authorization: Bearer <JARVIS_LOCAL_API_TOKEN>`),
// so the gh-app sends BOTH — Authorization satisfies proxy.ts, X-GH-App-Token
// satisfies this route. The caller must still clear proxy.ts's Host allowlist
// — deployment config, not handled here.
//
// TOKEN-PASSING DESIGN: the gh-app mints a repo-scoped ~1h GitHub App
// INSTALLATION token and passes it in; we thread it through
// launchContainerSession (→ setSessionContainer) into the session's container
// meta, where the scoped git proxy + the host-side PR path pick it up. The web
// never mints tokens (it holds no App private key), and the token never enters
// the container. Jobs run in minutes — well inside the token's lifetime.
// Spec: docs/superpowers/specs/2026-07-03-jarvis-gh-app-code-session-design.md

/** machine_name marker for the bot's dedicated environments. User /code envs
 *  use "Cloud container" (environments/cloud route), so this can never collide
 *  with — or be picked up instead of — an env the user configured. */
const BOT_ENV_MACHINE = "gh-app-bot";

function serviceTokenOk(req: Request): boolean {
  const expected = process.env.GH_APP_BRIDGE_TOKEN ?? "";
  if (!expected) return false;
  const given = req.headers.get("x-gh-app-token") ?? "";
  const a = Buffer.from(expected, "utf8");
  const b = Buffer.from(given, "utf8");
  return a.length === b.length && timingSafeEqual(a, b);
}

export async function POST(req: Request): Promise<NextResponse> {
  if (!serviceTokenOk(req)) {
    return bridgeError(401, "unauthorized", "Service token required");
  }
  const body = (await req.json().catch(() => null)) as {
    repo?: string;
    installationToken?: string;
    botLogin?: string;
    task?: string;
    publicOrigin?: string;
    model?: string;
  } | null;
  const repo = typeof body?.repo === "string" ? body.repo.trim() : "";
  const installationToken =
    typeof body?.installationToken === "string" ? body.installationToken.trim() : "";
  const botLogin = typeof body?.botLogin === "string" ? body.botLogin.trim() : "";
  const task = typeof body?.task === "string" ? body.task.trim() : "";
  const publicOrigin =
    typeof body?.publicOrigin === "string"
      ? body.publicOrigin.trim().replace(/\/+$/, "")
      : "";
  const model =
    typeof body?.model === "string" && body.model.trim() ? body.model.trim() : undefined;

  if (!repo || !validRepoFullName(repo)) {
    return bridgeError(400, "invalid_request", 'repo must be "owner/name"');
  }
  if (!installationToken) {
    return bridgeError(400, "invalid_request", "installationToken is required");
  }
  // Required: external commits/PRs must be attributed to the App bot identity,
  // never fall through to the box owner's connected GitHub login.
  if (!botLogin) {
    return bridgeError(400, "invalid_request", "botLogin is required");
  }
  if (!task) {
    return bridgeError(400, "invalid_request", "task is required");
  }
  // The origin becomes the child's callback base + every session link, so it
  // must be an explicit, PARSEABLE http(s) origin with a real host — NEVER
  // derived from req.url (this is an internal docker-network call; links must
  // use the browser-facing host). new URL() rejects degenerate shapes like
  // "https://" that a bare prefix regex would wave through.
  let originOk = false;
  try {
    const u = new URL(publicOrigin);
    originOk = (u.protocol === "http:" || u.protocol === "https:") && u.hostname.length > 0;
  } catch {
    originOk = false;
  }
  if (!originOk) {
    return bridgeError(400, "invalid_request", "publicOrigin must be an http(s) origin");
  }

  try {
    const store = getStore();
    // Env get-or-create for the external repo — but a DEDICATED bot env only
    // (keyed on BOT_ENV_MACHINE, per repo), NEVER a user-created /code env for
    // the same repo. Reusing a user env would hand the bot job the user's
    // secret envVars + setup script and the default `full` network level
    // (--network=host → reachable loopback services). Owner = the box's real
    // user (JARVIS_GH_APP_SESSION_OWNER, e.g. the human's account id) so the bot
    // sessions appear in THAT user's /code UI and are watchable/steerable; falls
    // back to LOCAL_USER_ID (dev / unset) like other service-registered rows.
    const sessionOwner = process.env.JARVIS_GH_APP_SESSION_OWNER?.trim() || LOCAL_USER_ID;
    const repoUrl = `https://github.com/${repo}`;
    const existing = listEnvironments(store, sessionOwner).find(
      (e) =>
        e.worker_type === "container" &&
        e.machine_name === BOT_ENV_MACHINE &&
        e.git_repo_url === repoUrl,
    );
    let environmentId: string;
    if (existing) {
      environmentId = existing.environment_id;
    } else {
      environmentId = createEnvironment(store, {
        machine_name: BOT_ENV_MACHINE,
        directory: "/workspace",
        git_repo_url: repoUrl,
        max_sessions: 4,
        worker_type: "container",
        user_id: sessionOwner,
      }).environment_id;
    }
    // (Re)assert the locked-down bot config on EVERY dispatch — allowlist
    // egress (`trusted`, not full/host) and no env vars / setup script — so a
    // drifted or hand-edited bot env can't weaken isolation for the next job.
    setEnvironmentConfig(store, environmentId, {
      envVars: {},
      setupScript: "",
      networkLevel: "trusted",
      customAllowlist: [],
    });

    const sessionId = randomBytes(8).toString("hex");
    getOrCreateSession(store, sessionId, environmentId, task.slice(0, 80));

    // Seed BEFORE launch (the child replays inbound from seq 0, in order):
    // bot jobs run autonomously → bypassPermissions first, then the task.
    const permUuid = randomUUID();
    appendInbound(store, sessionId, {
      type: "control_request",
      uuid: permUuid,
      request_id: permUuid,
      request: { subtype: "set_permission_mode", mode: "bypassPermissions" },
    });
    const uuid = randomUUID();
    appendInbound(store, sessionId, {
      type: "user",
      uuid,
      session_id: sessionId,
      parent_tool_use_id: null,
      message: { role: "user", content: [{ type: "text", text: task }] },
    });
    appendSessionEvent(store, sessionId, {
      type: "user_prompt",
      payload: { type: "user_prompt", prompt: task, uuid },
    });

    // Fire-and-forget like runRoutine — the gh-app polls the session for
    // completion. installationToken/botLogin land in the container meta via
    // launchContainerSession → setSessionContainer (nowhere else).
    void launchContainerSession(store, {
      sessionId,
      repoFullName: repo,
      // baseUrl stays PUBLIC (the browser session URL). internalBaseUrl is how
      // the container reaches this app for git-proxy + callback: on the
      // containerized deploy the public origin is Cloudflare-fronted (bot
      // challenge) + host.docker.internal is unreachable, so name the web
      // service over the shared bridge. Unset (dev) → host.docker.internal path.
      baseUrl: publicOrigin,
      internalBaseUrl: process.env.JARVIS_CODE_INTERNAL_ORIGIN || process.env.GH_APP_WEB_URL,
      model,
      installationToken,
      botLogin, // validated non-empty above
    }).catch(() => {});

    return NextResponse.json({
      session_id: sessionId,
      session_url: `${publicOrigin}/code/session_${sessionId}`,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return bridgeError(500, "internal_error", `dispatch failed: ${msg}`);
  }
}
