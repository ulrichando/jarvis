# JARVIS-web CCR backend for /ultraplan (and teleport) — design

Status: DESIGN (Phase B1 of the CLI-utils unlock; Phase A shipped on
`claude/review-jarvis-utilities-oyki90`). Author date: 2026-06-27.

## Problem

The JARVIS CLI (`src/cli/`) is a copy of Claude Code's external build. Its
`/ultraplan` command and the `teleport` family are **intact clients** of
Anthropic's CCR (Claude-Code-Remote) cloud API: they create a remote session,
poll its `SDKMessage` event stream, and wait for the user to approve a plan in a
browser modal. Today they target Anthropic's cloud (`getOauthConfig().BASE_API_URL`),
which `start.sh`'s eBPF `IPAddressDeny` firewall blocks — so the command is dark
(gated behind `JARVIS_ULTRAPLAN=1`, off by default; see `commands/ultraplan.tsx`).

Goal: make `/ultraplan` work against **JARVIS's own web app** instead of
Anthropic's cloud, without modifying the intact CLI client message shapes.

## What already exists (do NOT rebuild)

`src/web` is a near-complete claude.ai/code-parity backend. `src/web/src/lib/bridge/store.ts`
(better-sqlite3) already implements the entire data model:
- **environments** (machines + cloud/container), per-user auth via `bridge_tokens`
  (`getOrCreateBridgeToken` / `resolveBridgeToken`), `createEnvironment`,
  `listEnvironments`, `ensureDefaultCloudEnv`.
- **work queue** with lease/heartbeat (`enqueueWork`/`leaseNextWork`/`heartbeatWork`)
  — the worker dispatch substrate.
- **sessions** (`getOrCreateSession`, `setSessionTitle`, `archiveSession`,
  `setSessionToken`/`validateSessionToken`, `bumpWorkerEpoch`, container + worker-spec
  resume machinery).
- **events** (`appendSessionEvent`, `listSessionEvents(sinceRowid)` — monotonic
  `rowid` cursor), **inbound** (`appendInbound`/`listInboundSince`), internal events.

Existing HTTP surface under `src/web/src/app/api/bridge/v1/`:
- `sessions` GET(list)/POST(create→`{id}`); `sessions/{id}` GET/PATCH/DELETE;
  `sessions/{id}/events` GET(`?since=rowid`)/POST; `sessions/{id}/archive` POST;
  `sessions/{id}/plan` GET (read-only — parses ExitPlanMode from events);
  `sessions/{id}/messages` POST (accepts `mode: 'plan'`, control_request/response).
- `environments` GET/DELETE, `environments/cloud` POST, `environments/bridge` POST.
- `code/sessions/{id}/worker/{register,events,heartbeat}` + `worker` GET/PUT — the
  worker-facing write path (per-session ingress token auth via `validateSessionToken`).
- Browser `/code` page (`src/web/src/app/(app)/code/[[...session]]/page.tsx`) with a
  session list, event-stream view, and a **read-only plan panel** (polls `/plan`).

So the backend, worker substrate, and most of the UI exist. The work is a thin
**CCR-compat adapter** + a **plan-approval** path, not a new backend.

## Protocol contract the CLI client expects

From `src/cli/src/utils/teleport/api.ts` + `teleport.tsx` + `utils/ultraplan/ccrSession.ts`:
- `POST /v1/sessions` — body `{title, events[], session_context, environment_id}`;
  the initial `events[]` carries a `control_request: set_permission_mode {mode, ultraplan:true}`.
- `GET /v1/sessions/{id}/events?after_id=<cursor>` → `{ newEvents: SDKMessage[],
  lastEventId, sessionStatus, branch }`. **`after_id`/`lastEventId` are opaque to the
  client** — it only echoes the cursor back. (Key simplification: the existing `rowid`
  cursor works as-is, stringified — no UUID layer needed.)
- `POST /v1/sessions/{id}/events` — send a user message.
- `POST /v1/sessions/{id}/archive`.
- `GET /v1/environment_providers` (+ `…/cloud/create`) — list/seed environments.
- Auth today: `Authorization: Bearer <claude.ai OAuth token>` + `x-organization-uuid`
  + `anthropic-beta: ccr-byoc-2025-07-29`. Client hard-refuses when
  `JARVIS_DISABLE_AUTH=1` (`getTeleportAuthMessage`, `api.ts:22-28`).

The ultraplan poller (`ExitPlanModeScanner`, `ccrSession.ts`) scans the stream for an
`assistant` `tool_use` named `EXIT_PLAN_MODE_V2_TOOL_NAME` followed by a `user`
`tool_result`: `is_error:false` + `## Approved Plan:` marker → approved; `is_error:true`
+ `__ULTRAPLAN_TELEPORT_LOCAL__` sentinel → execute-locally; other `is_error:true` →
rejected (iterate).

## Design — add a CCR-compat route group, reuse the store

Adapt the **server** to the client (client stays unmodified). Add a new route group
`src/web/src/app/api/v1/` (NOT under `/api/bridge`) that mirrors the CCR paths and
delegates to the existing `store.ts` helpers + reuses the existing worker substrate.

### B2 — routes (all thin wrappers over store.ts)
1. `POST /v1/sessions` — parse `{title, events, session_context, environment_id}`.
   Resolve/define the environment (default to a single local "bridge"/"cloud" env via
   `ensureDefaultCloudEnv`), `getOrCreateSession`, mint + `setSessionToken`, persist the
   initial `events` (incl. the `set_permission_mode` control_request) via `appendInbound`,
   enqueue worker work (`enqueueWork`). Return `{id, ...}` (and the session token if the
   same process won't run the worker).
2. `GET /v1/sessions/{id}/events` — read `after_id` (stringified rowid; `0`/absent = start),
   call `listSessionEvents(sinceRowid)`, return `{ data: SDKMessage[], last_id: String(maxRowid),
   session_status }`. Filter out `env_manager_log`/`control_response` (the poller already
   skips them, but filtering server-side keeps the contract clean). Map the session's
   worker state → `session_status` (`running`/`idle`/`requires_action`/`archived`).
3. `POST /v1/sessions/{id}/events` — `appendInbound` the user message.
4. `POST /v1/sessions/{id}/archive` — `archiveSession`.
5. `GET /v1/environment_providers` (+ `cloud/create`) — list from `listEnvironments`
   shaped as `{kind:'bridge'|'anthropic_cloud', environment_id, name, state:'active'}`;
   `cloud/create` → `ensureDefaultCloudEnv`. A single local environment is enough.

Cursor: expose `rowid` as the opaque `last_id`/`after_id` string. No schema change.

### B3 — point the client at jarvis-web + no-auth path (`src/cli`)
- `constants/oauth.ts`: add a `JARVIS_CCR_BASE_URL` override consumed by
  `getOauthConfig().BASE_API_URL` (default to the local jarvis-web origin, e.g.
  `http://127.0.0.1:3000`). Reconcile the path prefix — the client calls `/v1/...`, so the
  new route group is mounted at `/v1/...` (not `/api/bridge/...`).
- `utils/teleport/api.ts`: when `JARVIS_DISABLE_AUTH=1`, skip the claude.ai-OAuth refusal
  and send the JARVIS bridge bearer (`bridge_tokens`) instead of an Anthropic OAuth token;
  drop the `x-organization-uuid`/`anthropic-beta` requirement server-side (the compat
  routes accept any non-empty bearer, matching the existing permissive bridge routes).
- `start.sh`: allow the jarvis-web origin through the `systemd-run` `IPAddressAllow` list
  (loopback already allowed if web runs locally), and flip `JARVIS_ULTRAPLAN=1` once this
  lands so the command surfaces.

### B4 — worker (mostly exists)
The session's agent must run in **plan mode** and emit `assistant`/`user`/`result`
`SDKMessage`s incl. the `ExitPlanMode` tool_use. The CLI in `--feature=BRIDGE_MODE`
(already enabled in `start.sh`) registers as a worker and writes via
`code/sessions/{id}/worker/events`. Verify it: (a) leases work enqueued by `POST /v1/sessions`,
(b) honors the `set_permission_mode ultraplan` control_request, (c) writes the ExitPlanMode
tool_use the poller scans. If a gap exists, add a thin worker loop that drives a normal CLI
session in plan mode against the seeded prompt and writes events to the store.

### B5 — browser PlanModal + approval POST (`src/web`)
The `/plan` GET + plan panel already render the plan markdown. Add:
- `POST /v1/sessions/{id}/plan` (or reuse `messages` with a control_response) that appends
  a `user` `tool_result` for the pending ExitPlanMode tool_use:
  - **Approve** → `is_error:false`, content `## Approved Plan:\n<text>` (+ `(edited by user)`
    variant when edited) — exactly what `extractApprovedPlan` expects.
  - **Reject** → `is_error:true` (no sentinel) — the scanner iterates.
  - **Run locally** → `is_error:true` with `__ULTRAPLAN_TELEPORT_LOCAL__\n<text>`.
- Plan-modal UI on the `/code` page: render on the `plan_ready` phase (pending ExitPlanMode
  with no result) with Approve / Edit+Approve / Reject / Run-locally buttons POSTing the above.

## Verification (end-to-end)
- Unit: `ExitPlanModeScanner` (`ccrSession.ts`) is pure — feed it synthetic
  `SDKMessage[]` for approved / rejected / teleport / terminated and assert the verdicts.
- Route: hit the new `/v1/sessions*` routes with recorded CCR payloads; assert the event
  envelope shape (`{data, last_id, session_status}`) and that a seeded `set_permission_mode`
  survives round-trip.
- E2E: with jarvis-web running locally and `JARVIS_ULTRAPLAN=1`, run `/ultraplan "<task>"`
  from `bin/jarvis`; confirm a session appears on `/code`, the plan renders, Approve returns
  the plan text to the terminal, and "Run locally" teleports it back via the sentinel.

## Scope / risks
- IN: `src/web/src/app/api/v1/**` (new compat routes + plan POST), `src/web` plan-modal UI,
  `src/cli/src/utils/teleport/api.ts` + `constants/oauth.ts` (base-URL + no-auth path),
  `start.sh` (allow-list + `JARVIS_ULTRAPLAN=1`).
- OUT: the CLI client message shapes (`teleport.tsx`, `ccrSession.ts`) — unchanged.
- Risk: the worker execution path is the least-certain piece (B4) — verify before building
  the UI. `src/web`'s Next.js is a forked/non-standard version (see `src/web/AGENTS.md`) —
  read `node_modules/next/dist/docs/` before writing route handlers.
- This is a multi-part build; land B2+B3 (client talks to backend, events round-trip) and
  prove the worker (B4) before B5 (UI).
