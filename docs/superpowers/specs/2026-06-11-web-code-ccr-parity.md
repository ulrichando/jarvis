# JARVIS `/code` — claude.ai/code (CCR) parity

**Date:** 2026-06-11
**Status:** DRAFT — awaiting approval before build
**Scope:** `src/web` (build) + `src/cli` (read; modify only with sign-off)

## 1. How claude.ai/code works (research)

Task → Anthropic clones the repo into an isolated **cloud sandbox VM** (filesystem
+ network restrictions) → **Claude Code runs autonomously** → git via a **secure
proxy** (scoped credential, pushes only to the configured branch) → **automatic PR
+ change summary** → review → merge. Plus **parallel tasks**, **real-time progress**,
**mid-task steering**, **local handoff**.
Sources: claude.com/blog/claude-code-on-the-web, anthropic.com/engineering/claude-code-sandboxing.

## 2. The protocol — CCR v2 (Claude Code Remote)

The mechanism is a plain-HTTP server↔worker protocol. **JARVIS already implements
both halves**, mapped 1:1 to claude.ai/code:

| Role | claude.ai/code | JARVIS | State |
|---|---|---|---|
| CCR **server** | Anthropic CCR | web `api/bridge/v1/*` + `lib/bridge/store.ts` | ✅ endpoints exist |
| **worker** ("machine") | Anthropic cloud VM / your local CLI | `src/cli/bridge/{bridgeApi,sessionRunner,codeSessionApi}.ts` | ✅ exists |
| **the UI** | claude.com/code | `/code/page.tsx` | ❌ disconnected stub |

**Worker lifecycle (CCR v2), already implemented in `src/cli/bridge/bridgeApi.ts`:**
1. `POST {baseUrl}/v1/environments/bridge` — register (machine_name, directory,
   branch, git_repo_url, max_sessions, worker_type).
2. `GET {baseUrl}/v1/environments/{envId}/work/poll` — long-poll for work.
3. run the agent (`sessionRunner` spawns the Claude session) →
   `POST {baseUrl}/v1/sessions/{sessionId}/events` (progress).
4. `work/{id}/ack` · `/heartbeat` · `/stop` · `sessions/{id}/archive` ·
   `environments/{id}/bridge/reconnect`.

**The `baseUrl` switch:** point the worker at `claude.ai` (auth = claude.ai OAuth;
`CCR_MIRROR`/`CCR_AUTO_CONNECT` = the "activate remote" behavior) OR at JARVIS web
(`http://127.0.0.1:3000/api/bridge`) for the self-hosted path. Same protocol.

## 3. Current state — what's real vs missing

- ✅ Web server endpoints: `environments/bridge` (+`[envId]`), `work/poll`,
  `work/{id}/{ack,heartbeat,stop}`, `sessions/{id}/{events,archive}`,
  `environments/{id}/bridge/reconnect`, `admin/enqueue`.
- ✅ `bridge.db` schema: `environments`, `work`, `sessions`, `session_events`.
- ✅ CLI worker: register/poll/run/report loop + `sessionRunner` (permissions,
  activities, stderr) + `codeSessionApi` (CCR v2 code-session, OAuth).
- ❌ **`/code` UI**: `onSubmit` is a no-op; machine picker = "coming soon"; toolbar
  buttons disabled; never calls the API; **0 environments/sessions ever created**.
- ❌ **No read/list endpoints for the UI**: list environments, list sessions,
  stream a session's events (SSE), enqueue a task from the UI.
- ❌ **No review surface**: diff viewer, PR creation, change summary.

## 4. Plan (phased; each phase ships + is verified live)

**Phase 0 — connect a worker (validate the loop).** Document + script pointing a
local `jarvis` CLI worker's `baseUrl` at JARVIS web; confirm it registers an
environment + polls. *(Likely a `src/cli` config touch — see §5.)*

**Phase 1 — dispatch + observe (the core thread).** Web only:
- `GET /api/bridge/v1/environments` (list) + machine picker (replaces "coming soon").
- Composer `onSubmit` → enqueue work (`admin/enqueue` or a new `tasks` endpoint) to
  the chosen environment.
- Session view: SSE stream of `session_events` → real-time progress (mirror
  claude.ai/code's progress tracker).
- Acceptance: type a task → worker runs it → UI shows live progress to completion.

**Phase 2 — review + accept.** Diff viewer of the session's changes; "create PR"
(via the workspace git layer / GitHub) + change summary; accept/merge.

**Phase 3 — parallel + steer.** Multi-session dashboard (parallel tasks across
environments); send a follow-up message to a running session (mid-task steering);
stop/archive controls.

**Phase 4 — polish.** Routines (saved tasks), network config, search, "new window".

## 5. `src/cli` touchpoints — the `/remote-control` connection

The connection mechanism ALREADY EXISTS: the `/remote-control` command +
`remoteBridgeCore.ts`. It is "not connected" to JARVIS only because it is
**hardwired to Anthropic's CCR**:
- `isBridgeEnabled()` / `isClaudeAISubscriber()` **require a claude.ai subscription**
  + the claude.ai OAuth token (excludes API-key/Bedrock/Vertex/Console logins).
- the `baseUrl` override (`CLAUDE_BRIDGE_BASE_URL`) is **`USER_TYPE === 'ant'` only**.
- build-time `BRIDGE_MODE` GrowthBook flag.

**Phase 0 `src/cli` change (REQUIRES user sign-off):** add a self-hosted path so
`/remote-control` can connect to JARVIS web —
1. allow `baseUrl = http://127.0.0.1:3000/api/bridge` without the `ant`-only gate
   (e.g. a `JARVIS_BRIDGE_BASE_URL` env or a `--bridge-url` flag);
2. when the baseUrl is the local JARVIS server, auth with the **JARVIS local bearer
   + `environment_secret`** instead of demanding claude.ai OAuth;
3. keep the claude.ai path untouched (additive — don't break upstream remote-control).

This is the ONE real `src/cli` change. Everything else (Phases 1–4) is `src/web`.
- **events**: confirm `session_events` carry enough for the progress UI (tool calls,
  file edits, status) — add fields only if the UI needs them.

## 6. Open questions
1. Self-hosted auth: reuse the local bearer + `environment_secret`, or add a CCR-lite
   token? (Phase 0.)
2. Execution target: only local `jarvis` CLI workers, or also a web-spawned sandbox
   (reuse the workbench's workspace+exec machinery) as a "machine" with no CLI?
3. Review/PR: go through GitHub (needs a connected repo + token) or stay
   workspace-diff-only for v1?
4. Does the worker run on the **repo you point it at** (any local dir), matching
   claude.ai/code's "connect a repo"? (Yes via `directory`/`git_repo_url` — confirm.)
