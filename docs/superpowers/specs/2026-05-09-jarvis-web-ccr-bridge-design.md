# jarvis-web CCR-compat bridge API — design

> **Goal.** Build the 9 `/api/bridge/v1/*` Next.js API routes in `src/web/` so a CLI invoking `/remote-control` can register against jarvis-web (`http://127.0.0.1:3000`) instead of Anthropic's Claude Code Remote (`bridge.claudeusercontent.com`). This is **sub-project 1** of a 4-part effort to wire the jarvis CLI's bridge to a local jarvis-web hub. Sub-projects 2 (CLI wiring), 3 (web UI), and 4 (E2E polish) follow once this surface is stable.

**Date:** 2026-05-09
**Author:** ulrich (with Claude)
**Status:** draft → for review
**Branch:** `feat/ext-browser-control-v3`

---

## Context

The jarvis CLI fork already ships ~20 files of bridge-client code at `src/cli/src/bridge/`. That client speaks a polling-based HTTP protocol against Anthropic CCR endpoints (full inventory in `src/cli/src/bridge/bridgeApi.ts`). The fork's `start.sh` blocks the public CCR domain at the kernel layer and uses provider-key auth (Groq/DeepSeek), so the original `isBridgeEnabled()` gate (claude.ai OAuth + `tengu_ccr_bridge` GrowthBook flag) always returns false. Commit `5ae05ec` removed that runtime gate so `/remote-control` surfaces in the CLI's slash-command picker; invoking it currently fails because there's no compatible server on the loopback side.

This spec defines the server side: a Next.js API surface that mimics the relevant parts of CCR's protocol. The CLI talks to it unchanged (sub-project 2 only changes the base URL + auth-token plumbing).

## Non-goals

- **No upstream-CCR compatibility beyond the 9 endpoints.** The fork's `bridgeApi.ts` is the only client; we don't need to support every CCR header or rate-limit shape.
- **No multi-user auth.** Loopback-only. No OAuth, no SSO, no JWT identity. Single-user assumption.
- **No remote-machine bridge.** Both ends run on `127.0.0.1`. Adding LAN/cross-host support is sub-project ≥4.
- **No claude.ai-style web composer routing.** Sub-project 1 stops at the API surface — the `/code` page UI lands in sub-project 3.
- **No `tengu_*` GrowthBook compatibility shims.** The CLI's `isBridgeEnabled()` runtime check is already bypassed in commit `5ae05ec`.

---

## Architecture

```
                    ┌──────────────────────────────────────────┐
                    │  jarvis-web (Next.js, port 3000)         │
                    │                                          │
   CLI ───HTTP─────▶│  /api/bridge/v1/environments/bridge      │
   (port-anything)  │  /api/bridge/v1/environments/{id}/...    │
                    │  /api/bridge/v1/sessions/{id}/...        │
                    │       │                                  │
                    │       ▼                                  │
                    │  bridgeStore  (SQLite, ~/.jarvis/bridge.db)
                    │       │                                  │
                    │       ▼                                  │
                    │  bridgeEvents (in-memory EventEmitter)   │
                    └──────────────────────────────────────────┘
```

### Module layout

| Path | Responsibility |
|:--|:--|
| `src/web/src/app/api/bridge/v1/environments/bridge/route.ts` | POST register; DELETE unregister (with `[id]` sub-path) |
| `src/web/src/app/api/bridge/v1/environments/[envId]/work/poll/route.ts` | GET long-poll for work |
| `src/web/src/app/api/bridge/v1/environments/[envId]/work/[workId]/ack/route.ts` | POST ack |
| `src/web/src/app/api/bridge/v1/environments/[envId]/work/[workId]/stop/route.ts` | POST stop (force flag) |
| `src/web/src/app/api/bridge/v1/environments/[envId]/work/[workId]/heartbeat/route.ts` | POST heartbeat (extends lease) |
| `src/web/src/app/api/bridge/v1/environments/[envId]/bridge/reconnect/route.ts` | POST reconnect (session_id) |
| `src/web/src/app/api/bridge/v1/sessions/[sessionId]/events/route.ts` | POST permission-response events |
| `src/web/src/app/api/bridge/v1/sessions/[sessionId]/archive/route.ts` | POST archive (idempotent) |
| `src/web/src/lib/bridge/store.ts` | SQLite data layer (better-sqlite3); synchronous, no I/O outside DB |
| `src/web/src/lib/bridge/events.ts` | In-memory `EventEmitter` keyed by environment_id |
| `src/web/src/lib/bridge/auth.ts` | Bearer-token validation (loopback + secret) |
| `src/web/src/lib/bridge/__tests__/store.test.ts` | Unit tests (vitest) |
| `src/web/src/lib/bridge/__tests__/integration.test.ts` | End-to-end happy-path test using fetch |

### Storage

SQLite at `~/.jarvis/bridge.db` via `better-sqlite3` (already in jarvis-web's deps; check before adding).

```sql
CREATE TABLE IF NOT EXISTS environments (
  environment_id TEXT PRIMARY KEY,
  environment_secret TEXT NOT NULL,        -- self-issued bearer token
  machine_name TEXT NOT NULL,
  directory TEXT NOT NULL,
  branch TEXT,
  git_repo_url TEXT,
  max_sessions INTEGER NOT NULL DEFAULT 4,
  worker_type TEXT NOT NULL DEFAULT 'jarvis',
  created_at INTEGER NOT NULL,             -- Unix ms
  last_seen_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS work (
  work_id TEXT PRIMARY KEY,
  environment_id TEXT NOT NULL REFERENCES environments(environment_id) ON DELETE CASCADE,
  session_id TEXT NOT NULL,                -- the CLI session that will run this work
  state TEXT NOT NULL,                     -- 'pending' | 'leased' | 'done' | 'stopped'
  data_json TEXT NOT NULL,                 -- WorkData (prompt, attachments, etc.)
  secret_b64url TEXT NOT NULL,             -- base64url(JSON({ session_ingress_token, api_base_url, ... }))
  leased_at INTEGER,                       -- Unix ms; null until acked
  lease_expires_at INTEGER,                -- Unix ms
  created_at INTEGER NOT NULL
);
CREATE INDEX work_env_state ON work(environment_id, state);

CREATE TABLE IF NOT EXISTS session_events (
  event_id TEXT PRIMARY KEY,               -- nanoid
  session_id TEXT NOT NULL,
  type TEXT NOT NULL,                      -- 'permission_response' for now
  payload_json TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX session_events_session ON session_events(session_id, created_at);

CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY,
  environment_id TEXT NOT NULL,
  archived INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  archived_at INTEGER
);
```

Lease TTL: 60 seconds. Heartbeat extends to `now + 60_000`. On poll, `reclaim_older_than_ms` query param marks expired leases pending again before checking for new work.

### In-memory event bus

`bridgeEvents` is a process-local `EventEmitter` keyed by `environment_id`. The `poll` handler subscribes to `work-available:${envId}` for at most 25 seconds before resolving null. New `pending` work in the store emits the event so a long-poll picks it up immediately. Process restart is acceptable: the next poll cycle reads from SQLite, so no work is lost — just a one-shot delay.

**Note on Next.js timeouts:** route handlers are exempt from the default 30 s response timeout when running under `bun` or Node directly, but Vercel/serverless deployments cap at 10 s by default. Since this server is loopback-only and self-hosted (`npm run dev` or `next start`), 25 s is safe. If a future deployment moves to a serverless provider, drop to 8 s and rely on the CLI's natural retry loop.

### Auth model

- **Register (`POST /v1/environments/bridge`)** is **un-authenticated** — anyone on loopback can register. Only access risk on this endpoint: a hostile process on the same machine could register a phantom environment. Acceptable for v1 (single-user dev box).
- **All other endpoints** require `Authorization: Bearer <environment_secret>` matching the secret returned at register. The secret is 32 random bytes, base64url-encoded. Validated by `lib/bridge/auth.ts` against the `environments` table. Mismatch → 401.
- **Loopback-only bind:** the spec assumes Next.js dev server's default `0.0.0.0` bind. Operator must bind to `127.0.0.1` if exposed beyond localhost is undesirable. Documented in spec, not enforced in code.

### CORS

Same-origin only. No `Access-Control-Allow-Origin` headers.

---

## Endpoints

Full request/response shapes follow the CLI client's expectations in `src/cli/src/bridge/bridgeApi.ts` and `src/cli/src/bridge/types.ts` (the source of truth for the wire format).

### 1. POST `/api/bridge/v1/environments/bridge` — register

**Request:**
```json
{
  "machine_name": "kali-laptop",
  "directory": "/home/ulrich/Documents/Projects/jarvis",
  "branch": "feat/ext-browser-control-v3",
  "git_repo_url": "https://github.com/ulrichando/jarvis.git",
  "max_sessions": 4,
  "metadata": { "worker_type": "jarvis" },
  "environment_id": null
}
```

If `environment_id` is provided AND found in the DB, returns the existing record (idempotent re-register on `--session-id` resume). Otherwise creates a new one with a freshly generated `environment_id` (nanoid, 16 chars) and `environment_secret` (32 random bytes → base64url).

**Response 200:**
```json
{ "environment_id": "abc123...", "environment_secret": "..." }
```

**Errors:** 400 on missing required fields; 500 on DB failure.

### 2. GET `/api/bridge/v1/environments/{envId}/work/poll` — long-poll

**Auth:** Bearer `<environment_secret>` (looked up from the env record).

**Query params:** `reclaim_older_than_ms` — optional integer; if set, mark `leased` work where `lease_expires_at < now - reclaim_older_than_ms` back to `pending` first.

**Behavior:**
1. Reclaim expired leases (if param set).
2. Try to lease the oldest `pending` work for this env. If found, set state `leased`, `leased_at = now`, `lease_expires_at = now + 60_000`. Return the row.
3. If none, subscribe to `bridgeEvents.once('work-available:${envId}')`. Wait up to 25 seconds. If signaled within window, retry the lease. If timeout, return null.

**Response 200:**
```json
{
  "id": "work_abc",
  "type": "work",
  "environment_id": "env_abc",
  "state": "leased",
  "data": { ... WorkData ... },
  "secret": "<base64url JSON>",
  "created_at": "2026-05-09T10:00:00.000Z"
}
```
Or `null` (HTTP 200 with body `null`) on no work available.

**Errors:** 401 (bad bearer), 404 (env not found), 410 (env unregistered).

### 3. POST `/api/bridge/v1/environments/{envId}/work/{workId}/ack` — ack

**Auth:** Bearer `<environment_secret>`.
**Request body:** `{}` (empty).
**Behavior:** No state change beyond a `last_seen_at` bump. Idempotent. CLI calls this after fully accepting the work locally.
**Response 204** (no content).

### 4. POST `/api/bridge/v1/environments/{envId}/work/{workId}/stop` — stop

**Auth:** Bearer `<environment_secret>`.
**Request body:** `{ "force": boolean }`.
**Behavior:** Mark work as `stopped` in the store. Server-side this is a hint — the CLI will stop processing.
**Response 204**.

### 5. POST `/api/bridge/v1/environments/{envId}/work/{workId}/heartbeat` — heartbeat

**Auth:** Bearer `<environment_secret>`.
**Request body:** `{}`.
**Behavior:** If the work is in state `leased` and not expired, set `lease_expires_at = now + 60_000`. Returns the updated state.
**Response 200:**
```json
{ "lease_extended": true, "state": "leased", "last_heartbeat": "...", "ttl_seconds": 60 }
```
If the lease has already expired or work is stopped: `lease_extended: false`.

### 6. POST `/api/bridge/v1/environments/{envId}/bridge/reconnect` — reconnect session

**Auth:** Bearer `<environment_secret>`.
**Request body:** `{ "session_id": "<uuid>" }`.
**Behavior:** Marks the session as alive (bumps `last_seen_at` on both the env and the session row). Used when the CLI was disconnected and rejoins.
**Response 204**.

### 7. POST `/api/bridge/v1/sessions/{sessionId}/events` — permission-response events

**Auth:** Bearer `<session_ingress_token>` from the WorkSecret. Note: this is **not** the env secret. The token is per-session and lives in `WorkSecret.session_ingress_token` (base64url-encoded JSON returned with each work item).

**Request body:** `{ "events": [PermissionResponseEvent, ...] }`.

**Behavior:** Persist each event into `session_events` table. No fan-out for v1 (sub-project 3 will subscribe via SSE on the web side).

**Response 204**.

### 8. POST `/api/bridge/v1/sessions/{sessionId}/archive` — archive session

**Auth:** Bearer `<environment_secret>`.
**Request body:** `{}`.
**Behavior:** Set `archived=1`, `archived_at=now`. Idempotent — if already archived, return 409 (the client treats 409 as success).
**Response 204** on first archive, **409** if already archived.

### 9. DELETE `/api/bridge/v1/environments/bridge/{envId}` — unregister

**Auth:** Bearer `<environment_secret>`.
**Behavior:** Delete the env record. CASCADE-deletes its work rows. Sessions are kept (they may be archived later) but their state shows unregistered.
**Response 204**.

### Error response shape

All non-204/200 responses use this body so the existing CLI error parser (`extractErrorDetail`, `extractErrorTypeFromData` in `bridgeApi.ts`) works unchanged:

```json
{ "error": { "type": "environment_expired", "detail": "Environment 410'd: not found" } }
```

Status codes the client recognizes (handled in `handleErrorStatus`):
- 401 → fatal (bad token)
- 403 → fatal (denied / expired)
- 404 → fatal (env not found)
- 410 → fatal (env expired) — type `environment_expired`
- 429 → retryable (rate-limit)
- 500+ → retryable

---

## WorkSecret format

When work is created (sub-project 3 will provide the UI to enqueue work; for v1 we expose a tiny POST `/api/bridge/v1/admin/enqueue` route gated behind the loopback assumption to seed test data), the server constructs a `WorkSecret` and base64url-encodes it as the `secret` field. Shape mirrors `bridge/types.ts::WorkSecret`. The `WorkData` shape (the `data` field on the work envelope) is treated as opaque JSON by the server in v1 — the CLI controls its structure (prompt, attachments, tool config) and the server passes it through unchanged. Validation can be added in sub-project 3 once the UI defines the canonical creation path.

```json
{
  "version": 1,
  "session_ingress_token": "<32-byte random b64url>",
  "api_base_url": "http://127.0.0.1:3000/api/bridge",
  "sources": [],
  "auth": [{ "type": "noop", "token": "" }],
  "claude_code_args": null,
  "mcp_config": null,
  "environment_variables": null,
  "use_code_sessions": false
}
```

The `session_ingress_token` is what the CLI will use as the bearer for `POST /sessions/{id}/events`. Stored in the same `work` row's `secret_b64url` column for symmetric validation when events arrive.

---

## Tests

### Unit (`store.test.ts`)
- Register: idempotent on same `environment_id`; new IDs produce new rows.
- Lease: oldest `pending` first; lease prevents double-lease.
- Lease reclaim: expired leases go back to `pending`.
- Heartbeat: extends `lease_expires_at`; rejects on stopped work.
- Archive: idempotent (returns "already archived" sentinel).
- Auth: secret mismatch returns null/false.

### Integration (`integration.test.ts`)
- Spawn a Next.js test server on a random port.
- Run the full happy-path with `axios` against the real routes:
  1. Register → assert response shape, secret returned.
  2. Enqueue test work via the admin route.
  3. Poll → returns the work, leased.
  4. Heartbeat → `lease_extended: true`.
  5. Send session event → 204, row in `session_events`.
  6. Archive → 204; archive again → 409.
  7. Unregister → 204; subsequent poll → 410.
- Bonus: long-poll without work waits ~25s and returns null.

No tests for performance, concurrent multi-environment, or LAN exposure.

---

## Open questions deferred to sub-projects 2-4

- **Sub-project 2:** What env var name does the CLI use to point at this server? (Probably `JARVIS_BRIDGE_BASE_URL=http://127.0.0.1:3000/api/bridge`.) Where does it set `environment_secret` as the access token (replacing the `getClaudeAIOAuthTokens()` path)?
- **Sub-project 3:** Web UI design — does the `/code` page show registered environments live? SSE or polling? Composer → `enqueue work` flow?
- **Sub-project 4:** What does an end-to-end smoke test look like? Probably: `jarvis /remote-control` → web shows machine → web composer "open new file" → CLI receives, runs, streams events back.

---

## Verification

1. `cd src/web && npm test -- bridge` (vitest filtered to bridge tests) → all unit + integration green.
2. `curl -s -X POST http://127.0.0.1:3000/api/bridge/v1/environments/bridge -H 'Content-Type: application/json' -d '{"machine_name":"test","directory":"/tmp","max_sessions":1,"metadata":{"worker_type":"test"}}'` → returns `{ environment_id, environment_secret }`.
3. `curl -s http://127.0.0.1:3000/api/bridge/v1/environments/<ID>/work/poll -H "Authorization: Bearer <SECRET>"` → returns null (no work) within ~25s.
4. CLI dry-run from sub-project 2: `JARVIS_BRIDGE_BASE_URL=http://127.0.0.1:3000/api/bridge jarvis /remote-control` → no fatal errors.
