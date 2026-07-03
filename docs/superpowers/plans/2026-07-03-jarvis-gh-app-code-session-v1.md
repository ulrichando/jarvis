# JARVIS gh-app × /code (v1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.
> **READ FIRST:** the design spec `docs/superpowers/specs/2026-07-03-jarvis-gh-app-code-session-design.md` and the `/code` system map in that spec's companion exploration. `src/web` is **stock Next.js 16.2.6** — before touching any route, read `src/web/node_modules/next/dist/docs/01-app/01-getting-started/15-route-handlers.md` + `16-proxy.md`. Route handlers take `ctx: { params: Promise<...> }` (await it); middleware is `src/web/src/proxy.ts`.

**Goal:** `@jarvis-gh-bot <task>` runs as a real, watchable jarvis `/code` session (clone via the scoped git-proxy, autonomous run, PR opened by the host) with the **session URL stamped into the PR + tracking comment**, replacing the throwaway sandbox.

**Architecture:** gh-app worker → `POST /api/bridge/v1/gh-app/dispatch` (new, service-token) → `runRoutine`-style create (env for the external repo + injected installation token → seed `bypassPermissions` + the task → `launchContainerSession` with an explicit public origin) → poll session status → `POST /sessions/{id}/pr` (stamped) → gh-app updates the tracking comment with the session link + PR. Fallback to the sandbox behind `GH_APP_USE_CODE_SESSIONS` for one release.

---

## Conventions
- Web tests: `cd src/web && <its test cmd>` (check package.json; likely `npm test`/vitest). gh-app tests: vendored bun `… /vendor/bun/linux-x64/bun test src/gh-app/`.
- **Pathspec commits.** Injected-deps for anything hitting network/DB/docker. Additive-only in shared code paths (normal `/code` must stay byte-identical when no external token / no service call is present).
- Build on a worktree off `origin/master` (gh-app + /code both live there). Symlink `src/cli/node_modules` for gh-app parse/tests.

## Phase A — web: external-repo + injected installation token (additive, no `/code` regression)

### Task A1: git-proxy uses a per-session injected token when present
**Files:** `src/web/src/lib/bridge/store.ts` (container meta), `src/web/src/app/api/bridge/v1/code/sessions/[sessionId]/git/[...path]/route.ts:60`, `src/web/src/lib/connectors/github.ts`
- [ ] Extend the container meta (the blob that already carries `gitCapToken`, `store.ts:833-856` / set at `containers.ts:351-356`) with optional `installationId?: number` (store the **installation id, not a raw token** — App tokens expire in 1h; mint fresh per request).
- [ ] In the git route, replace the single `const pat = await getGithubToken()` (line 60) with: if the session meta has `installationId`, mint a fresh installation token for `meta.repo` (repo-scoped) via the gh-app token logic; else `getGithubToken()` (unchanged for normal `/code`). Add a small server-side minter (port `appJwt`+`installationToken` from `src/gh-app/token.ts`, or call the gh-app's internal mint endpoint) reading the App creds from the gh-app creds path/volume.
- [ ] TDD: injected `installationId` → the forwarded GitHub request carries the minted installation token; no `installationId` → uses the global PAT (existing behavior, a regression guard). Repo-scope (`getSessionGitScope`/`assertRepoAllowed`) needs no change — cover an out-of-scope 403 still holds.

### Task A2: host-side PR/committer use the injected token + bot identity for external jobs
**Files:** `src/web/src/lib/connectors/github.ts` (`openPullRequest`/`githubPrStatus`), `src/web/src/lib/bridge/containers.ts:365,383-384,876-884`
- [ ] Add an **optional token param** to `openPullRequest` (+ `githubPrStatus`) — when passed, authenticate with it instead of `load().github.token`. Default path unchanged.
- [ ] In `createContainerPR` (`containers.ts:834-888`): when the session has `installationId`, mint a token and pass it to `openPullRequest`; committer identity (git config at 383-384) = the App bot (`jarvis-gh-bot[bot]`) instead of `githubStatus().login`.
- [ ] TDD: external-job PR opens with the installation token + bot identity; normal `/code` PR unchanged.

## Phase B — web: session-URL stamping + the cross-service dispatch route

### Task B1: stamp the session URL into the PR (and commit trailer)
**Files:** `src/web/src/lib/bridge/containers.ts:846-847,876-884`
- [ ] `createContainerPR`: append to the PR body and the in-container commit message (line 858 script) the session URL — derived as it is for the env at line 611 (`<publicOrigin>/code/session_<id>`) — plus a `Jarvis-Session: <url>` trailer. Keep current title/body as defaults; only append.
- [ ] TDD: the opened PR body + commit message contain the `/code/session_<id>` URL.

### Task B2: new internal dispatch route (service-token; runRoutine-style)
**Files:** Create `src/web/src/app/api/bridge/v1/gh-app/dispatch/route.ts`; reuse `src/web/src/lib/bridge/routines-run.ts:25-102` pattern; touch `src/web/src/proxy.ts` (Host allowlist) + `.env.production`.
- [ ] `POST` handler: authenticate a `GH_APP_BRIDGE_TOKEN` bearer (constant-time compare; reject otherwise — do NOT use `getUserId`). Payload `{ repo, installationId, task, publicOrigin, model? }`.
- [ ] Body: env get-or-create for the external repo (mirror `environments/cloud` create with `worker_type:"container"`, `git_repo_url` from `repo`, owner = the box's single user), create session, persist `installationId` into the container meta, seed inbound like `runRoutine` (`set_permission_mode: "bypassPermissions"`, then the `user` message with the task), then `launchContainerSession({ sessionId, repoFullName: repo, baseUrl: publicOrigin, model })`. Return `{ session_id, session_url }` (`session_url = <publicOrigin>/code/session_<id>`).
- [ ] **Host allowlist:** the gh-app calls `web:3000` internally → add the compose service host to `JARVIS_WEB_ALLOWED_HOSTS` (env, `.env.production`) so `src/proxy.ts` (91–105/196–204) doesn't 403. Verify `JARVIS_LOCAL_API_TOKEN` is also sent if `JARVIS_REQUIRE_LOCAL_AUTH=1`.
- [ ] TDD: valid service token + payload → session created, `installationId` persisted, `launchContainerSession` invoked with the public origin; bad/absent token → 401; never calls `getUserId`.

## Phase C — gh-app: use /code sessions instead of the sandbox

### Task C1: gh-app dispatch client + worker wiring
**Files:** `src/gh-app/codeSession.ts` (new), `src/gh-app/worker.ts`/`server.ts`, `src/gh-app/jobs.ts` (persist `session_url`)
- [ ] `createCodeSession(job, deps)`: `POST <WEB_INTERNAL_URL>/api/bridge/v1/gh-app/dispatch` with `GH_APP_BRIDGE_TOKEN` (+ `JARVIS_LOCAL_API_TOKEN`), body `{repo, installationId, task, publicOrigin: GH_APP_PUBLIC_CODE_ORIGIN}` → `{session_id, session_url}`. `pollUntilDone(sessionId, deps)`: `GET /api/bridge/v1/sessions/{id}` (or `/events` `worker` field) until status `done`/`needs_input`/timeout. `openSessionPr(sessionId, deps)`: `POST /api/bridge/v1/sessions/{id}/pr` → PR url.
- [ ] Behind `GH_APP_USE_CODE_SESSIONS` (default on once proven): the worker calls `createCodeSession` → persist `session_url` → `pollUntilDone` → `openSessionPr`; the old `runInSandbox` stays as the fallback branch. Injected deps (fetch) — hermetic tests.
- [ ] TDD: worker creates a session, persists the url, polls, opens the PR; a service-call failure marks the job failed (best-effort feedback still runs).

### Task C2: feedback shows the watchable session link
**Files:** `src/gh-app/feedback.ts` (from the deployed feature)
- [ ] `workingMessage` → include "▶︎ Watch it live: `<session_url>`". `resultMessage` ok → include the session link alongside the PR (`✅ Opened #N · [watch the run](<session_url>)`).
- [ ] TDD: the working + result comments contain the session URL when present.

## Phase D — deploy + live E2E (held)
- [ ] Set `GH_APP_BRIDGE_TOKEN` (both services), `JARVIS_WEB_ALLOWED_HOSTS += web`, `GH_APP_PUBLIC_CODE_ORIGIN=https://0wlan.com`, `GH_APP_USE_CODE_SESSIONS=1` in `.env.production`; the gh-app compose service needs the App creds volume already present.
- [ ] Validate: `docker compose config` parses; web + gh-app suites green; normal `/code` still works (create a session the old way — regression check).
- [ ] **HELD (live):** `@jarvis-gh-bot fix X` on `maxrun` → a `/code` session appears at `0wlan.com/code/session_<id>`, watchable, opens a PR whose body links back to the session. Human-run.

## Self-review / risks
- Additivity: A1/A2/B1 must leave normal `/code` byte-identical when no `installationId`/token/override is present — every task has a regression test for that.
- **1-hour token:** store `installationId`, mint per-request in the proxy/PR path — never a raw token at rest (SQLite).
- **Origin:** always use the explicit `publicOrigin`, never `req.url`, for `baseUrl`/session-url (avoids internal-host links + broken child callbacks).
- **Two auth layers:** the gh-app must pass both `src/proxy.ts` (Host allowlist + local token) and the new route's service-token.
- **DB is in-process SQLite:** the gh-app integrates over HTTP only; the token minting for the proxy lives inside the Next process.
