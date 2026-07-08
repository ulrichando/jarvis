# JARVIS web app review (`src/web`) — findings + fix plan

**Written against commit:** `77b6e21a` (review run 2026-07-06).
**Status:** 4 of 14 fixed on this branch (#1, #6, #7, #9) plus the settings-tab half of #13; the rest are self-contained specs to resume.

This is the resume artifact for a `src/web` advisor review. Each finding below has
file:line evidence, a fix sketch, effort, and risk — enough for a fresh session (or a
weaker executor) to pick up with zero prior context. Verify each cite still matches
(the tree may have moved) before editing.

## How to resume

1. Read this file.
2. Pick a finding from the table. The **DONE** rows are already in the branch — skip them.
3. Implement the fix sketch. Match the surrounding code's conventions.
4. Verify: `cd src/web && npx tsc --noEmit && npx vitest run <relevant test dir>` and
   `npm run build`. Add a regression test next to the existing ones (see the two DONE
   rows for the pattern).
5. The security/auth rows (2, 3, 4) can 401-only-online — verify against a live request
   path, not just tests (see the repo memory "remote-control-proxy-selfauth-gap").

## Status table (leverage order)

| # | Finding | Cat | Eff | Conf | Status |
|---|---------|-----|-----|------|--------|
| 1 | Workspace file routes follow symlinks out of the sandbox | SEC | S-M | HIGH | **DONE** |
| 2 | Session events GET has no ownership check | SEC | S | HIGH | TODO |
| 3 | `admin/enqueue` unauthenticated + stale loopback comment | SEC | S | HIGH | TODO |
| 4 | PTY websocket falls open on loopback + host-shell fallback | SEC | S | HIGH | TODO |
| 5 | Chat stage-progression fires on a stale `submit` closure | BUG | S | HIGH | TODO |
| 6 | Settings write non-atomic + defaults-fallback wipes keys | BUG | S | HIGH | **DONE** |
| 7 | Sidebar reads every session's full transcript per 6s poll | PERF | S | HIGH | **DONE** |
| 8 | MCP client connections leak on forced-image / error paths | BUG | S | HIGH | TODO |
| 9 | Failed container launch leaks egress-proxy container + net | BUG | S | HIGH | **DONE** |
| 10 | Bridge auth hand-rolled per route + drifted (2 is one case) | DEBT | M | HIGH | TODO |
| 11 | No `typecheck` script; CI `tsc` job non-blocking (clean today) | DX | S | HIGH | TODO |
| 12 | `db:migrate` trap + `dotenv` phantom dep on `shadcn` | DX/DEP | S | HIGH | TODO |
| 13 | God-components: settings-tab (3046) + chat.tsx (1925-L fn) | DEBT | M-L | HIGH | TODO — settings-tab split **DONE**; chat.tsx extraction remains (needs char tests) |
| 14 | Per-viewer `docker exec` + git diff every 5s per session | PERF | M | HIGH | TODO |

Direction (options, not bugs): Kimi K2 modes (built+tested, flag off 2mo — ship or
shelve); workspace app-user mgmt (scaffold `authorize()` accepts any creds); AI SDK
family one major behind (needs char tests first).

**Dependency order:** char tests for the chat route + code-composer must land before the
AI SDK v7 bump and before extracting chat.tsx (#13). Fix #2 as a point patch, then fold
into #10.

---

## 1. Workspace symlink escape — **DONE**

`lib/workspace/storage.ts:485` `resolveSafe` was lexical only (`path.resolve` +
`startsWith`); its docstring falsely claimed symlinks were rejected. The workspace is
bind-mounted into the `/code` container where the agent has a shell, so an in-workspace
symlink pointing OUT (e.g. at `~/.jarvis/keys.env`) passed the check → arbitrary host
file read/write as the web-server user. **Fix shipped:** added `assertRealpathInside`
(realpath the target, or nearest existing ancestor for creates, and re-assert containment
against the real root). Regression test in `tests/workspace/storage.test.ts`.

## 2. Session events GET has no ownership check — TODO

`api/bridge/v1/sessions/[sessionId]/events/route.ts:57` — GET returns the full transcript
for any `sessionId` with no owner/credential check (the POST in the sibling file IS
guarded). Under multi-user Cloudflare Access this is cross-user transcript disclosure by
id iteration. **Fix:** add the same ownership check the diff route uses
(`lib/bridge/authz.ts::authorizeSession` / `authSessionOwner`); the /code page polls this
GET same-origin (`components/code/code-session.tsx:642`) so the check must accept the
session cookie / bridge token that poll carries — verify online, not just in tests. Also
check the v1 twin `api/v1/sessions/[sessionId]/events` POST (only checks a bearer is
present, not valid). **Risk MED** — a too-strict check 401s the live /code poller.

## 3. `admin/enqueue` unauthenticated — TODO

`api/bridge/v1/admin/enqueue/route.ts:7` — no auth, comment says access control "relies
on 127.0.0.1 bind"; the box is now Cloudflare-fronted so that precondition is false. No
web caller exists in-repo (only the proxy.ts comment references it). **Fix:** require a
resolvable bridge token whose user owns `environment_id` (mirror the sibling
`api/bridge/v1/sessions` POST owner check), or retire the route if the CLI sub-project-3
path doesn't use it. Update the stale comment. **Risk LOW-MED** — confirm no live caller
depends on the open path first.

## 4. PTY websocket falls open on loopback + host-shell fallback — TODO

`scripts/pty-server.mjs:61` — `REQUIRE_AUTH` is on only when `JARVIS_PTY_REQUIRE_AUTH=1`
or the bind is off-loopback; default loopback = no token needed. `:147` — when docker
isn't detected it spawns `$SHELL` on the HOST. Any co-located process reaching `:8772/pty`
gets a credential-free shell. **Fix:** default `REQUIRE_AUTH` on (the browser already mints
the HS256 `pty-token` via `api/workspace/[id]/pty-token`); gate the host-shell "local"
mode behind an explicit opt-in and disable it when `JARVIS_REQUIRE_LOCAL_AUTH=1`.
**Risk MED** — verify local dev terminal + `bin/jarvis-computer-use` still mint/send a
token before flipping the default, or local terminals break.

## 5. Chat stage-progression stale closure — TODO (flagship "builds drift" bug)

`components/chat/chat.tsx:1611` fires `queueMicrotask(() => submit(progressPrompt, ...))`
using the SAME `submit` closure that just finished; `submit` at `:746` reads
`const historyForApi = [...messages, userMessage]` from the render closure — which does
NOT include the stage-1 user prompt + assistant reply that were just `setMessages`'d. The
server builds the model context solely from the request body (`api/chat/route.ts:413`),
so stage 2 is sent without the plan → the model restarts/invents a plan. The auto-continue
path at `:1337` does it correctly (builds `messagesForRequest` explicitly). **Fix:** keep a
`messagesRef` updated every render (`useEffect(() => { messagesRef.current = messages })`)
and read `messagesRef.current` at `:746`, OR thread the finalized history into the
continuation like the auto-continue loop. **Risk LOW-MED** — chat.tsx streaming has a
documented regression history; add a test asserting the stage-2 request body includes the
stage-1 turns. **Confirmed by reading both ends.**

## 6. Settings non-atomic write wipes keys — **DONE**

`lib/settings/store.ts` — `saveSettings` did a direct `fs.writeFile` (torn on crash) and
`loadSettings` fell back to `DEFAULT_SETTINGS` on a broken file, which the next save then
persisted — wiping every provider key. **Fix shipped:** atomic temp+rename write, keep a
`.bak` of the prior good file, and `loadSettings` now tries `settings.json` → `.bak` →
legacy before defaulting. Regression tests in `tests/settings-store.test.ts`.

## 7. Sidebar full-transcript N+1 — **DONE**

`api/bridge/v1/sessions/route.ts:41` — `listSessionEvents(store, s.session_id, 0)` reads
ALL events for each of up to 40 sessions on every 6s poll, using only `first` (first
`user_prompt`) and `last`. **Fix:** add two point queries to `lib/bridge/store.ts`
(`firstUserPrompt(sessionId)` via `MIN(rowid) WHERE type='user_prompt'`, `lastEvent(sessionId)`
via `MAX(rowid)`) and batch the `findEnvironment` calls into one `WHERE environment_id IN (...)`.
Preserve the exact response shape. **Risk LOW.** Sibling: `api/v1/sessions/route.ts:118`
is unbounded (no LIMIT) + 2 queries/row — same fix family.
**Fix shipped:** `lib/bridge/store.ts` gained `firstUserPrompt` / `lastEvent` point
queries, a batched `findEnvironments(WHERE environment_id IN (...))`, and an optional
`limit` on `listSessions`; the GET in `api/bridge/v1/sessions/route.ts` now uses all
three (response shape unchanged, full existing bridge suite green). NOT covered: the
`api/v1/sessions` sibling was left untouched — its unbounded scan is still open.

## 8. MCP client connection leak — TODO

`api/chat/route.ts:711` connects MCP servers per plain-chat request; `mcpClose` is only
awaited in `streamText`'s `onFinish` (`:876`). The forced-image early return (`:759`,
triggers on the Image toggle or `hasImageIntent`) returns without calling `mcpClose`, and
a provider error (`onError`, `:854`) skips `onFinish`. **Fix:** hoist cleanup into one
helper called on every exit — forced-image return, questionnaire return, onFinish, onError
(double-close is already `.catch`-guarded). **Risk LOW.**

## 9. Failed container launch leaks egress proxy + network — **DONE**

`lib/bridge/containers.ts:317` — `step()`'s failure handler only `rm -f`s the workbench
container, not the `jarvis-egress-<id>` squid container or `jarvis-net-<id>` network
created at `:391`. The orphan sweep (`:1273`) skips non-`jarvis-code-` names, so the egress
containers are never reaped. A launch failing before `setSessionContainer` leaves
`container_json` NULL so idle-reclaim never sees it either. **Fix:** in `step()`'s catch,
run the full teardown trio from `stopContainerSession` (`:1221`) — rm proxy + `network rm`
+ container; and let the sweep map `jarvis-egress-<id>` back to sessions. **Risk LOW**
(cleanup is best-effort).
**Fix shipped:** `containers.ts` gained `teardownSessionDocker` (container → egress proxy
→ network, each rm best-effort) called from both `step()`'s failure handler and
`stopContainerSession`; `runOrphanContainerSweep` now maps `jarvis-egress-<id>` names back
to sessions too, deduped per session. Regression tests in
`tests/bridge/containers-launch-teardown.test.ts` (8 tests: pre/mid-flight failure runs
the trio in container-before-network order, teardown errors never mask the step error,
sweep reaps orphaned egress proxies but spares live/fresh ones).

## 10. Bridge auth drift — TODO (fold #2 in here)

~18 bridge route files re-implement the `extractBearer → resolveBridgeToken /
validateEnvSecret / isSharedLocalToken` ladder with divergent rules; the same fail-open
class has been patched one-route-at-a-time ≥3× (see commit comments "security review
2026-07-06"). **Fix:** expand `lib/bridge/authz.ts` into a single
`authorizeBridgeRequest(req, { scope })` covering all four credential types; convert routes
one group at a time against a recorded auth-matrix test. **Risk MED** — the CLI worker uses
several credential types (env secret, session token, bridge token, shared infra token);
capture the current matrix in tests first.

## 11. Add `typecheck` script + ratchet CI — TODO

No `typecheck` in `package.json`; `.github/workflows/lint.yml:100` runs
`next typegen && tsc --noEmit` with `continue-on-error: true`. `tsc --noEmit` is clean
today. **Fix:** add `"typecheck": "next typegen && tsc --noEmit"` (typegen first — Next 16
generates `RouteContext` types), flip `continue-on-error` off, fix the stale
`web-tests.yml` "bun test is non-blocking" comment, add a `check` script chaining
lint+typecheck+test. **Risk LOW** (passes today).

## 12. `db:migrate` trap + `dotenv` phantom dep — TODO

`drizzle/meta/_journal.json` has 5 entries but the live DB's `__drizzle_migrations` is
empty (schema applied via `drizzle-kit push`); `npm run db:migrate` replays `0000` into
existing tables and aborts. `next.config.ts:6` imports `dotenv` which isn't in
`package.json` — it resolves only via `shadcn@4.4.0` (a CLI mis-placed in `dependencies`).
**Fix:** baseline existing DBs (insert the 5 journal hashes into `__drizzle_migrations`) or
document push-only; add `dotenv` as a direct dep (or use Node's `process.loadEnvFile`) and
move `shadcn` to devDependencies. **Risk LOW** (verify table shapes match `0004` before
baselining).

## 13. God-components — TODO (needs characterization tests first)

`components/workbench/tabs/settings-tab.tsx` (3046 L, 15 self-contained sections, fan-in 1)
and `components/chat/chat.tsx` (1925-L function, fan-in 3: chat/workbench/design). **Fix:**
settings-tab is a mechanical split — one file per section under
`components/workbench/settings/` + a thin shell + `next/dynamic` import in the workbench
page (also fixes the hidden-section polling, finding 14-adjacent). chat.tsx needs
`useChatStream` / `useActionRunner` / artifact-panel hooks extracted, but ONLY after
characterization tests cover the SSE stream loop. **Risk: settings-tab LOW (pure move),
chat.tsx MED.**
**Fix shipped (settings-tab half only):** pure move of the 3046-line settings-tab into 17
files under `components/workbench/settings/` (shared.tsx + one per section) with a
304-line shell in `tabs/settings-tab.tsx`; the workbench page imports it via
`next/dynamic({ ssr: false })` like WorkbenchTerminal. Section bodies moved verbatim; the
shell's runtime/git/db polls are unchanged (pushing git/db down into Backups/Database is a
cheap follow-up). Render tests in `tests/settings/workbench-settings-tab.render.test.tsx`
(sidebar+composition, section mount/unmount, no hidden-section fetch). chat.tsx extraction
still TODO — blocked on characterization tests for the SSE stream loop.

## 14. Per-viewer docker exec + git diff every 5s — TODO

`components/code/code-session.tsx:725` polls `/diff?summary=1` every 5s unconditionally →
`containers.ts:988` spawns `docker exec … git add -A -N; git diff --stat` per poll (~720
spawns/hr/tab, mutates the index). The autofix/automerge ticks run the same per session
every 90s. **Fix:** server-side per-session memo of the summary with a ~5-10s TTL (shared
across viewers + ticks); skip polling when `worker_status` is idle and the last poll was
empty. **Risk MED** — TTL must not make the diff badge feel stale.

---

## Considered and rejected (don't re-audit)

- Evolution console disappearance — deliberate shelving (commit `b9e6880c`), not abandoned.
- `searxng/` dir — serves the voice-agent, not a duplicate search lib.
- `/api/v1/sessions` — intentional tested CCR-compat surface.
- `heartbeatWork` non-transactional SELECT→UPDATE — harmless (leaseNextWork ignores lease
  fields on pending rows).
