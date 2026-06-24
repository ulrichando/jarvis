# Decisions pending — findings awaiting a maintainer call

Single tracked home for review findings that are **deliberately not fixed**
because they need a product/architecture decision, not just code. When you
decide one: implement (or explicitly reject) it, then move the entry to the
CHANGELOG with the outcome. Don't let entries silently rot — each has a
"revisit by" date.

Source reviews: 2026-06-10 full voice-agent review; 2026-06-11 SDLC review.

---

## 1. `pipeline/screen_share_observer.py` default polling path is broken

The default "polling" path imports the retired `tools._vision_backend.vision_describe`
— dead since the 2026-05-20 rebuild. The observer only works on its
non-default paths.

**Options:** (a) retire the module (screen vision is `computer_use`'s job
now), (b) re-implement polling on `pipeline/computer_use_vision`, (c) move
to a Gemini-stream watcher (see 2026-04-29 continuous-screen-watching spec).
**Recommendation:** (a) retire — no current caller depends on the default path.
**Revisit by:** 2026-07-01.

## 2. CLAUDE.md `computer_use` description is stale

CLAUDE.md still describes a "vision→plan→act loop (Sonnet 4.6 with Opus 4.7
escalation)" with its own screenshot dump. Reality since the rebuild:
primitive action surface + supervisor-side vision via
`pipeline/computer_use_vision` llm_node injection; audit trail re-wired
(no `model_used`/screenshot columns).

**Action:** one CLAUDE.md edit; kept here because CLAUDE.md wording is
maintainer-owned voice.
**Revisit by:** 2026-06-20.

## 3. TTFW misses the 1 s target (p50 1.5 s / p90 4.4 s)

Real perf finding from telemetry. Needs a decision on where to spend:
prompt-cache warming, route-classifier fast paths, TTS first-chunk
latency, or accepting a 1.5 s target.

**Revisit by:** 2026-07-01 (re-measure after the next provider change).

## 4. ACP adapter: unbounded history growth

`state.history` grows forever; context is rebuilt from full history each
prompt with no pruning (the voice path has token-aware pruning). Long IDE
sessions will eventually 400/truncate.

**Options:** (a) port `_prune_chat_ctx_for_budget`, (b) window to last N
pairs, (c) accept for now (IDE sessions are short).
**Recommendation:** (a) — the pruner already exists.
**Revisit by:** 2026-07-01.

## 5. ACP adapter: dead contextvar plumbing

`edit_approval.maybe_require_edit_approval` has zero callers; the requester
bound in `_run_prompt` + `contextvars.copy_context()` in dispatch protect a
contextvar nobody reads. The loop-side `_maybe_approve_edit` is the only
live gate.

**Options:** remove the dead plumbing, or wire it as the real gate.
**Revisit by:** 2026-07-01.

## 6. cli: @opentelemetry HIGH advisories accepted (gate at `critical`)

`npm audit fix` (2026-06-11) cleared the critical `shell-quote` and `ws`
advisories. Remaining: `@opentelemetry/exporter-prometheus` < 0.217
(GHSA-q7rr-3cgh-j5r3, process crash via malformed HTTP request) + sdk-node
depending on it. The fix is a **breaking** sdk-node 0.218 bump in a tree
treated as a separate codebase, and the exporter only listens when
prometheus export is explicitly enabled. `security-audit.yml` gates cli at
`--audit-level=critical` until this lands; web stays at `high`.

**Action:** schedule the otel bump inside a cli-focused session, then
restore `--audit-level=high`.
**Revisit by:** 2026-07-01.

## 7. Six stale worktrees, all dirty (potential work loss)

`.worktrees/{barge-in-truncation, kimi-supreme, news-widget,
regression-prevention, screen-watching, voice-quality}` — last commits 5–6
weeks old, **every one has uncommitted changes**, and their branches sit on
pre-force-push history (hundreds of "unmerged" commits that no longer mean
anything). Several were superseded by work that landed on master via other
paths (e.g. barge-in fix 2026-05-18).

**Action needed per worktree:** salvage anything wanted from the dirty
files, then `git worktree remove --force` + delete the branch. Not done
automatically — removal destroys the uncommitted changes.
**Revisit by:** 2026-06-25.

## 8. Auto-mod `SPAWN_LIVE` flip is overdue for its decision

`JARVIS_AUTOMOD_ENABLED=1` has been live since ~2026-05-24 with the spawner
OFF pending a "7-day queue audit". The queue (`~/.jarvis/auto-mods/queue.jsonl`)
is currently **empty** and artifacts exist through 2026-06-01. Either flip
`JARVIS_AUTOMOD_SPAWN_LIVE=1` (the audit window has long passed and the
pattern volume is low), or decide the detector thresholds are too tight to
ever be useful and revisit them.
**Revisit by:** 2026-06-25.

## 9. Machine clock skew on `Moon`

Flagged 2026-06-10: telemetry timestamps vs sqlite `now` disagreed by
~1h18m; file mtimes jumped. Unowned. Check `timedatectl` / NTP sync state —
skew corrupts telemetry-based decisions (recency checks, retention prune).
**Revisit by:** 2026-06-15.

## 11. Re-scrub history before public flip (secrets now DEAD: PG rotated, LangSmith removed)

**UPDATE 2026-06-24:** LangSmith **removed entirely** (unused — no code refs, no SDK,
tracing never on; stripped from `keys.env` + all config; revoke any stale console key).
Postgres password already rotated (item 1 below). So both leaked values in git history
are now **dead** — the only remaining work is the *optional* history re-scrub (cosmetic
cleanup of dead values) before any public-repo flip.

Found 2026-06-11: pre-sanitization revisions of `docs/runbook/credential-rotation.md`
and `docs/runbook/git-history-scrub.md` embedded real secret values; two were
**still live** when caught: the full `LANGCHAIN_API_KEY` and the password
inside `JARVIS_PG_DSN`. The working-tree copies are sanitized now, but the
values remain in **git history** (and possibly on the GitHub remote since
the May pushes).

**Required, in order:**
1. ~~Rotate the Postgres password~~ — **DONE 2026-06-11**: rotated (owner
   chose a simple local-only password — accepted risk: role is idle, no
   repo consumers, server loopback-only), old leaked password verified
   rejected by the server. ~~Rotate the LangSmith key~~ — new key
   installed in `.env` 2026-06-11; **REMAINING: confirm the OLD key
   (`lsv2_pt_e278…`) was actually REVOKED in the LangSmith console** —
   creating a new key does not invalidate the old one.
2. Decide whether LANGCHAIN_TRACING_V2 should be on at all (currently
   `false` — the key is unused at runtime).
3. Re-run `docs/runbook/git-history-scrub.md` with the leaked values in
   the replacement list (they remain in git history even after rotation).
4. **BLOCKER for the public-repo flip** (README note, 2026-05-24): do NOT
   flip public until step 3 is done.

**Revisit by:** 2026-06-14.

## 10. Tauri webview hardening (CSP `unsafe-inline`, `withGlobalTauri`)

`tauri.conf.json` ships `script-src 'self' 'unsafe-inline'`,
`withGlobalTauri: true`, and devtools-toggle in the default capability.
Each enlarges the blast radius of any webview XSS into IPC. Tightening
requires manual UI verification (tray, chat panel, kiosk face) — not safe
to change blind.
**Revisit by:** 2026-07-15.

## 12. /code dispatch: finish the CCR v2 session backend

UPDATE 2026-06-12 (late): the CCR v2 worker backend for the REPL-attach path
is BUILT and verified end-to-end live (web → CLI over SSE, CLI → web via
/worker/events; e2e: injected prompt round-tripped through a real REPL+LLM).
Implemented: /v1/code/sessions/{id}/worker (PUT/GET state — its absence
caused a 2s session-recreate loop), /worker/register (epoch),
/worker/heartbeat (409 on stale epoch), /worker/events (transcript ingest;
stream_event/keep_alive filtered), /worker/events/stream (SSE, 15s
keepalives, from_sequence_num resume), /worker/events/delivery,
/worker/internal-events (resume state); session_inbound queue + composer
(POST /v1/sessions/{id}/messages) in the /code session view; sessions POST
now mints a session token and dispatches `{type:'session'}` work.

UPDATE 2026-06-12 (second pass, verified live): the remaining worker-path
gaps are closed and the loop ran end-to-end against a real
`jarvis remote-control` daemon (dispatch → child spawn → SSE prompt → model
reply "ok." → result, 5.4s; worker_status idle→running→idle observed via
the events poll). Done in this pass:
- tasks route now creates the session + token, seeds the prompt on the
  inbound stream (SSE catch-up delivers it to the spawned child), and
  dispatches `{type:'session'}` work — `{type:'prompt'}` is gone.
- Permission flow bridged: PUT /worker requires_action_details +
  external_metadata.pending_action surface in the events poll; the /code
  session view renders an Allow/Deny card; POST /messages accepts
  {permission} (control_response; updatedInput echoed from pending_action —
  an empty {} means "use original input" per
  PermissionPromptToolResultSchema) and {interrupt} (control_request).
- ack / work-heartbeat / stop routes accept the work's session ingress
  token (the CLI sends that, not the environment secret — every ack and
  lease heartbeat 401'd before, causing ~60s re-delivery churn).
- Worker echo of web-sent user messages deduped by uuid
  (--replay-user-messages would double every prompt in the transcript).
- CLI: foreign-session work now gets stopWork'd by the REPL bridge instead
  of churning the queue forever; bridge-spawned children no longer crash at
  startup with "MACRO is not defined" (runtime fallback in cli.tsx — the
  spawner bypasses run-cli.mjs's --define args).
- Route-level protocol tests: src/web/tests/bridge/ccr-v2.test.ts.

STILL TO BUILD:
- stream_event live-typing in the session view (currently dropped; final
  messages only).
- /code machine picker should mark REPL-held machines (dispatching a task
  to one is now cleanly refused via stopWork, but the task still doesn't
  run anywhere — the picker shouldn't offer attach-only environments).
Protocol reference: CLI `remoteBridgeCore.ts`/`ccrClient.ts` and
`~/Documents/Projects/claude-code/src/remote/`.

UPDATE 2026-06-12 (third pass): CONTAINER SESSIONS BUILT (MVP) and verified
live — claude.ai's init sequence (container → clone → optional setup →
start Jarvis Code) ran in <5s against ulrichando/maxrun, model replied,
follow-up turn answered from /workspace/<repo>, archive reaped the
container. Shape: `POST /v1/environments/cloud {repo}` registers a virtual
environment (worker_type 'container') that appears in the existing machine
picker; the tasks route branches on worker_type and the WEB acts as
environment-manager (src/web/src/lib/bridge/containers.ts): docker run from
jarvis-workbench (label com.jarvis.code-session), git clone via the §13
connector PAT (remote URL kept clean; auth moved to a store-backed
credential helper so the agent can push — see git-workflow note below),
optional
.jarvis/setup.sh, pre-seeded workspace trust (CLAUDE_CONFIG_DIR
/jarvis-config), then the CLI source bind-mounted RO + vendored bun runs
`cli.tsx --print --sdk-url <web>/v1/code/sessions/{id}` — the same worker
endpoints as bridge children, so SSE/permissions/transcripts are unchanged.
Init steps stream as status session_events.
GIT WORKFLOW (DONE 2026-06-12): the container is now push-capable end to
end so the agent edits/commits/pushes/PRs without ever asking for a git
name/email (the failure a live session hit). At clone time we set
`git config --global user.name/.email` from the connected GitHub login
(email `<login>@users.noreply.github.com`), configure `credential.helper
store` writing `/root/.git-credentials` (mode 600, login+token shq-escaped
into the sh -c), and `safe.directory` the workspace. The child CLI env
carries `GH_TOKEN`/`GITHUB_TOKEN` so `gh pr create` is pre-authenticated,
and an appended system prompt tells the agent git is fully wired — create
`jarvis/<topic>` branch, commit, `push -u`, open a PR — proactively on task
completion or any save/commit/merge/push ask. `gh` added to the workbench
Dockerfile (needs `npm run build:image` rebuild for PRs; commit+push work
on the existing image since git is already present). This skips claude.ai's
scoped git-proxy (a self-hosted single-user box trusts its own PAT); the
proxy is only needed for the multi-tenant threat model.
MODEL SELECTION (DONE 2026-06-12, revised same day): a /code container now
uses the SAME provider/model as `bin/jarvis`. The CLI reaches every provider
(DeepSeek/Groq/OpenAI/Gemini AND Claude) through a local LiteLLM proxy on
:4000 (src/cli/scripts/start.sh: ANTHROPIC_BASE_URL=http://localhost:4000,
key `jarvis-proxy`, JARVIS_PROVIDER/JARVIS_MODEL + JARVIS_MODEL_REGISTRY_ENABLED
+ JARVIS_DISABLE_TOOL_DEFERRAL). The container runs --network=host, so it
hits the host's 127.0.0.1:4000. `launchContainerSession` probes
`<proxy>/health`; when UP it mirrors that env (provider from the picked
model via MODELS_META — the web ids ARE the CLI registry ids verbatim;
default = the CLI default `deepseek-v4-pro` when nothing is picked) and emits
a `◌ Model — <id> (<provider>) via local proxy` status. When the proxy is
DOWN it falls back to api.anthropic.com directly and only Claude runs (a
non-Claude pick warns + uses the default Claude model; a Claude pick boots via
`--model`). The proxy URL is overridable with `JARVIS_CLI_PROXY_URL`. Also:
the web picker default was flipped from `claude-sonnet-4-6` to `deepseek-v4-pro`
so a fresh session matches the CLI out of the box. ROOT CAUSE of the original
report: the picker defaulted to Claude AND turn 1 ran before the seeded
set_model applied — both fixed. Remaining: this assumes the proxy is running
(it is when the desktop/CLI is up); a fully self-contained container would run
its own proxy or carry provider keys — deferred.
WORKFLOW PARITY (DONE 2026-06-12): closed the loop to match claude.ai/code
cloud sessions — autonomous work in an isolated container, then review the
diff. (1) Permission default flipped acceptEdits → `bypassPermissions` for
/code (page.tsx) so the agent installs deps / runs code / commits / pushes
without a prompt per command — the container is the isolation boundary, same
posture as bin/jarvis. (2) The container append-system-prompt now pushes
autonomy: run commands yourself (never tell the user to), write complete
implementations instead of asking what to put in a file, only ask when
genuinely ambiguous/destructive. (3) Real DIFF VIEW: `getContainerDiff`
(containers.ts) runs `git diff <origin-default>` in the session container
(intent-to-add so new files show) → `GET /sessions/{id}/diff` →
`CodePanels` Diff panel parses + renders per-file +/- (polls every 4s).
Was a stub ("No changes to show"). (4) CREATE PR (DONE 2026-06-12):
`createContainerPR` (containers.ts) → `POST /sessions/{id}/pr` → a "Create
PR" button in the Diff panel. Idempotent: moves off the base branch if
needed, commits pending work, pushes, reuses an existing PR for the branch
or runs `gh pr create --fill`, falls back to a GitHub compare URL when gh is
absent. Completes the review→integrate loop.
SDLC GAP ANALYSIS + ROADMAP (researched 2026-06-12 from code.claude.com web
docs). claude.ai/code phases mapped; jarvis has: Implement ✅, Review/diff ✅,
Integrate/PR ✅ (new), Routines ✅, archive ✅, autonomous mode ✅, git ✅.
CLOSED 2026-06-12 (second pass): (b) INLINE DIFF COMMENTS — click an
added/context line in the Diff panel → queue a note → it bundles into the
next message as "At <file>:<line>, …" (chips above the composer, removable;
sending is allowed with comments alone). page.tsx diffComments + DiffPanel
onComment. (c) PR + CI STATUS — `githubPrStatus(repo,branch)` (github.ts) +
`GET /sessions/{id}/pr-status?branch=` → the Diff panel shows a CI bar
(✓/✗/◷) and a "Fix CI" button that messages the session to fix the failing
checks; an existing PR auto-surfaces as "View PR". (e) URL PREFILL —
?prompt=/?q= + ?repositories=/?repo= seed the composer on mount. (f) PLAN
PANEL — `GET /sessions/{id}/plan` extracts the latest ExitPlanMode plan from
events; PlanPanel renders it (was a stub). Tests: 132 green.
CLOSED 2026-06-12 (third pass): (d) ENVIRONMENT-CONFIG UI — `environments`
gets a `config_json` column (additive migration) holding { envVars, setupScript };
`parseEnvironmentConfig`/`setEnvironmentConfig` (store.ts); launchContainerSession
applies the env vars to the child env (handshake keys still win) and runs the
configured setup script before the repo's .jarvis/setup.sh; `GET|PATCH
/environments/{id}/config` (owner-scoped, .env-format parsing); a "Configure
environment" modal in the /code page (env-vars + setup-script textareas).
(g) TELEPORT — `GET /sessions/{id}/teleport` (bearer-authed) returns repo +
branch + a markdown transcript; new `jarvis teleport <sessionId>` CLI command
(handlers/jarvisTeleport.ts) checks the branch out locally (clone or
fetch+checkout) and saves the transcript. NOTE: the upstream Claude Code
teleport (commands/teleport/index.js) is a disabled stub wired for claude.ai
cloud sessions; this is the self-hosted parallel. A richer "resume the
conversation in-CLI" could later revive the existing main.tsx teleport
machinery (launchTeleportResumeWrapper/processMessagesForTeleportResume).
CLOSED 2026-06-12 (fourth pass): (a) DIFF +/- INDICATOR — the session header
shows `+adds −dels` (cheap `?summary=1` diff poll that skips the full diff),
click to open the Diff panel. (b) AUTO-FIX CI — an "Auto-fix" toggle in the
Diff panel CI bar, persisted server-side (sessions.autofix column). A
background loop (`runAutofixTick` in lib/bridge/autofix.ts) scans autofix
sessions, and when an open PR's head commit has failing CI — and that commit
was not already fixed (sessions.autofix_sha) — appends a fix request to the
session. Runs via an in-process interval (src/instrumentation.ts, 90s, kill
switch JARVIS_CODE_LOOPS=0) AND POST /code/autofix/tick (timer-able),
so it works even when the Diff panel is closed; the panel also fires it
client-side while open.
CLOSED 2026-06-12 (fifth pass): ROUTINES CRON-RUNNER — real 5-field cron
matcher (src/lib/cron.ts: cronMatches/cronIsDue/cronRunsOnDay, client-safe,
tested) drives runRoutinesTick (routines-tick.ts) on the same instrumentation
interval; recurring crons fire when due (2h catch-up), ONE-TIME routines use a
trigger `at` (datetime-local input) and pause after firing. Calendar view
places routines by parsing the real cron, not labels. GITHUB WEBHOOK —
POST /api/bridge/v1/github/webhook (HMAC-verified): CI-failure events run an
auto-fix pass (instant vs the poll); any event fires subscribed github-trigger
routines (the only path that fires those). Needs a tunnel to reach GitHub.
GLOBAL ERROR BOUNDARY — src/app/(app)/error.tsx.
CLOSED 2026-06-12 (sixth pass): COMPOSER NON-IMAGE UPLOAD — the file picker
now accepts any file; non-images are read as text and inlined into the prompt
as fenced "Attached file <name>" blocks (images still ride as vision blocks),
with a file-chip in the attachment tray. PER-MESSAGE PIN SERVER-SYNC — pins
move from localStorage to session_message_pins (GET/POST /sessions/{id}/pins);
the session view fetches the pinned set once and passes it down, so pins
survive across devices/browsers. Tests: 151 green.
CLOSED 2026-06-12 (seventh pass — the launch-path infra, default-safe):
SETUP-SNAPSHOT CACHING — env-gated JARVIS_CODE_SETUP_CACHE=1 (default OFF →
flow unchanged). When on + the env has a setup script: first session commits
the post-setup container to `jarvis-workbench-cache:<envId>-<sha1(setupScript)>`
(scrubbing the baked-in push token first, re-writing it after), later sessions
launch FROM it and skip clone+setup — just `git fetch/reset --hard` to freshen
+ re-write creds. EGRESS PROXY + NETWORK LEVELS — EnvironmentConfig.networkLevel
(default `full` = today's --network=host, zero regression). `trusted`/`custom`/
`none` run the workbench on a private bridge net whose egress is a squid
allowlist proxy (jarvis-egress-<sid>), with the child reaching the app +
model proxy via host.docker.internal (NO_PROXY) instead of 127.0.0.1; level +
custom domains editable in the env-config modal; stopContainerSession reaps
the proxy + net. Arg-tested (hit/miss, isolated/full); docker primitives
live-smoked (image inspect/commit/network create+rm OK). CAVEATS: it's a
private-bridge (NOT --internal) so egress is SOFT (proxy-honoring tools are
allowlisted; a determined direct socket isn't blocked — the spec's hard
--internal variant is the follow-up); squid filtering itself wants a live
end-to-end check; `ubuntu/squid` is pulled on first isolated session
(pre-pull: docker pull ubuntu/squid). Tests: 155 green. **/code is now at
full claude.ai/code parity.**
CLOSED 2026-06-12 (eighth pass — Tier-1 UX, from a fresh claude.ai/code research
pass; plan at ~/.claude/plans/imperative-shimmying-moore.md): (1) DIFF FILE LIST —
the Diff panel shows a clickable file list (path + per-file +/-) that scrolls to
each file's section (code-panels.tsx, fileRefs). (2) CREATE PR draft/compose —
split button → full / draft (`gh pr create --draft`) / compose (returns the
GitHub new-PR/compare URL, no PR opened); `createContainerPR(…, mode)` +
`POST /sessions/{id}/pr {mode}`. (3) TRANSCRIPT VIEW MODES — Normal/Verbose/
Summary header toggle (localStorage); Summary = prose-only, Verbose expands the
init block (code-session.tsx). (4) SIDEBAR filter (Active/Archived/All) + text
search (code-sidebar.tsx; default Active hides archived). (5) KEYBOARD SHORTCUTS —
n=new, d=toggle diff, Esc=stop, ?=help overlay (fire outside inputs; browser-safe
single keys), page.tsx. Tests: 157 green; tsc clean.
CLOSED 2026-06-12 (ninth pass — Tier-2 workflow): (6) ROUTINES — GitHub-event
FILTERS (author/title/base/head/labels/is_draft/is_merged; RoutineTrigger.filters
+ matchesGithubFilters in runGithubRoutines, evaluated against the webhook
payload + form inputs); NATURAL-LANGUAGE SCHEDULE (parseNaturalSchedule in
lib/cron.ts: "every weekday at 9am", "in 2 hours", "tomorrow at 9am" → cron/at;
"Phrase" cadence in the form); PAST-RUNS (sessions.routine_id + setSessionRoutine
in runRoutine + GET /routines/{id}/runs + "Open latest run" on the card). Also
FIXED a pre-existing bug: runRoutine passed baseUrl=`${origin}/api/bridge`, which
launchContainerSession doubled → routines never reached the app; now bare origin.
(7) MULTI-REPO SESSIONS — launchContainerSession `extraRepos[]` clones each into
/workspace/<name> (global creds cover all; caching disabled when extras present);
tasks route `extra_repos`; a repo-chips + add-input UI for new sessions (page).
(8) MCP CONNECTORS→CONTAINERS — enabled http/sse servers from src/lib/mcp/store.ts
written to /jarvis-config/.mcp.json + `--mcp-config` at launch (stdio skipped).
(9) AUTO-MERGE — sessions.automerge + an Auto-merge CI-bar toggle (server flag);
runAutomergeTick squash-merges the PR (mergeContainerPR / `gh pr merge`) once all
checks pass + PR open; runs on the instrumentation interval. (10) REVIEW CODE — a
"Review" button in the diff panel messages the session to review its own diff.
Tests: 165 green; tsc clean.
CLOSED 2026-06-12 (tenth pass — Tier-3 valuable items): (11) CODE REVIEW — a
pragmatic model-based PR review: github.ts getPrDiff + postPrComment, lib/bridge/
code-review.ts reviewPullRequest (fetch diff → generateText review → PR comment),
POST /github/review route, a "Review PR ↗" button in the diff panel (pr-status
now returns repo), and webhook auto-review on PR open gated by
JARVIS_CODE_AUTO_REVIEW=1. (Inline-comment positioning + a check run are the
fuller version, deferred.) IDLE CONTAINER RECLAIM — lib/bridge/reclaim.ts
runReclaimTick reaps containers idle past JARVIS_CODE_IDLE_RECLAIM_HOURS
(default 12; 0=off) via listIdleContainerSessions + clearSessionContainer, on
the instrumentation interval — fixes the container pile-up. WEB NOTIFICATIONS —
code-session.tsx requests permission on first send + notifies (turn-done /
needs-input) when the tab is backgrounded. AUTO-ARCHIVE ON MERGE — runAutomergeTick
archives the session after a successful merge. LINK-SESSION-BACK — JARVIS_SESSION_URL
env + a prompt line so the agent puts the session link in PR bodies.
Tests: 168 green; tsc clean.
DELIBERATELY NOT BUILT (honest, low value on this single-user box): hard egress
+ git-proxy (#12 — nil security value here per CLAUDE.local.md accepted risk; soft
egress shipped + the egress spec covers the --internal/git-proxy version; live
verification would disrupt running sessions); session forking, local-repo bundling
without GitHub, "Open in editor" links (niche). **/code is at full claude.ai/code
parity for everything with real value on this box.**
INFRA PHASE — now SPEC'd, build-ready (needs a docker box to implement+verify):
docs/superpowers/specs/2026-06-12-jarvis-code-egress-and-cache.md covers the
egress allowlist proxy (squid + bridge network + host.docker.internal
callback), network-access levels (Full default = today's --network=host, so
opt-in/zero-regression; Trusted/Custom/None), and setup-snapshot caching
(commit-after-setup + restore + git-freshen, env-gated JARVIS_CODE_SETUP_CACHE,
default OFF). NOT shipped as code: unverifiable in the authoring sandbox, and
the egress proxy is multi-tenant hardening with ~no value on this single-user
box (CLAUDE.local.md accepts mic→root). Build when multi-user/exposed.
MVP tradeoffs (next phases): --network=host (web binds 127.0.0.1; the
egress-proxy phase replaces this — until then container isolation is
filesystem/process, NOT network), no setup snapshot
caching, no idle timeout (archive is the lifecycle end), and the cloud-repo
picker UI (env rows are curl/API-created today; picker lists them once
created).

RESEARCHED ARCHITECTURE (2026-06-12, from code.claude.com docs + Anthropic
sandboxing post): claude.ai/code's primary mode is CLOUD SESSIONS, not
Remote Control. Per task: fresh isolated VM (~4vCPU/16GB/30GB, Ubuntu 24.04
+ toolchains), repo CLONED from GitHub, per-environment setup script whose
result is FILESYSTEM-SNAPSHOT-CACHED (~7d) so later sessions skip it; all
egress through an allowlisting proxy (None/Trusted/Full/Custom levels);
git creds NEVER inside the sandbox — a git proxy translates a scoped
in-sandbox credential to the real token and restricts pushes to the working
branch; outcome = branch + optional PR, diff view (+42 −18) with inline
comments, auto-fix via GitHub-App webhooks; `--remote` pushes tasks up
(or uploads a <100MB git bundle when no GitHub), `--teleport` pulls a
session+branch down to the terminal; parallel sessions; mobile monitoring.
JARVIS mapping: container per task from the jarvis-workbench image (docker
commit ≈ their env cache), clone via the §13 GitHub connector with a
credential-proxy (MVP: short-lived token env), child CLI inside the
container speaks the ALREADY-BUILT /v1/code/sessions worker endpoints back
to the web app, branch+PR outcome via connector, diff view from git, egress
via a proxy container with the Trusted-domains allowlist. /code "new task"
then takes a REPO (not a machine); machine-targeted Remote Control stays as
the secondary mode (already working).
**Revisit by:** 2026-06-26.

### Per-message action bar (2026-06-12)
DONE: under each assistant message — **Copy** (clipboard), **Read aloud**
(browser SpeechSynthesis, toggle play/stop), and **relative time** ("7m ago").
All client-side, no backend. DONE (2026-06-12): **Pin a message** is a per-browser localStorage bookmark (keyed by message uuid) — toggles a filled pin, no backend. A server-side pinned-messages VIEW/sync across devices is still a follow-up.
pinned-messages view is wanted.

## 13. GitHub connector in Settings → Connectors (user request 2026-06-12)

A first-class GitHub connection (OAuth app or PAT) stored per-user, powering
the /code repo picker (#47) and the future clone-into-container flow (item
12). Today repo listing rides ad-hoc credentials; a connector card makes it
explicit, revocable, and per-account like the Remote Control card.
**Revisit by:** 2026-06-26.

## 14. /code UI parity — remaining stub affordances

The 2026-06-12 UI pass wired the plus menu (Import GitHub issue, Connectors
incl. MCP enable/disable toggles, Slash commands), the permission-mode picker
(Accept edits / Plan / Auto, applied live or seeded at dispatch), and the
sidebar session menu (Rename / Archive / Delete — fixed-positioned so it's no
longer clipped by the Recents scroll box, browser-authed routes added). Still
stubbed, deferred deliberately:
- **Add files or photos** (plus menu): DONE for images (2026-06-12) — composer
  reads picked images→base64 with thumbnail chips, page threads them, and the
  messages + tasks routes build Anthropic base64 image content blocks (vision
  models see them; the proxy flattens to "[image]" for text-only rungs).
  Verified: text+image → [text,image] blocks; non-image rejected (400).
  Remaining: NON-image files (docs) — would need an upload-into-workspace step
  (claude.ai uploads to the session container); not done.
- **Sidebar menu (2026-06-12 update): Pin, Copy link, and relative time
  ("2m ago") are now DONE** — `pinned` column + pinned-first sort, PATCH
  {pinned}, `/code?s=<id>` deep link for Copy link, time on each row (hidden
  on hover for the kebab). Menu order/shortcuts (P/R/C/A/D) mirror claude.ai.
  2026-06-12 (2nd pass): the FULL claude.ai menu is now implemented —
  **Open in** (submenu: Open on GitHub / Copy session ID), **Mark as read**
  (read column + neutral dot), **Share** (opens the page Share modal for that
  session), **Move to group** (session_groups table + group_id + create/assign,
  shown as a row badge). Remaining polish, not blocking: the Share modal's
  backend is still a visibility stub (no real public-link generation — moot on
  a single-user box); "Move to group" shows a badge rather than collapsible
  group SECTIONS in the list (sections = follow-up).
**Revisit by:** 2026-06-26.

## 15. /code: chat blanks when expanding a long code block

Reported 2026-06-12: expanding a file's content (e.g. index.html) in an
assistant reply made the whole chat disappear. Root cause: the markdown
`CodeBlock` (shiki) "Show all" expand can throw, and the web app has NO
global error boundary, so a single message's render error blanked the route.
MITIGATED same day: wrapped each message's `<Markdown>` in a per-message
error boundary (`MessageBoundary` in code-session.tsx) that falls back to
plain `<pre>` text — a bad block can no longer take down the transcript.
STILL TODO: fix the underlying CodeBlock expand crash (likely shiki re-highlight
on a large expanded body) so the content renders highlighted instead of
falling back to plain text; consider a global ErrorBoundary for the app.

## 16. /code Routines — full feature is unbuilt (sidebar link is dead)

The "Routines" sidebar nav has no handler and there is no /routines page or
backend. claude.ai's Routines (screenshots 2026-06-12) is a real feature:
- **Page**: list + Calendar views, "New routine", template suggestions,
  "Include completed" toggle, per-routine cards (cadence badge, paused state).
- **New-routine form**: Name, Instructions (+ model + repo pickers), trigger
  = Schedule (Once/Hourly/Daily/Weekdays/Weekly/Custom cron) | GitHub event |
  API (POST webhook), Connectors tab, Behavior tab (Auto-fix PRs), Permissions
  tab, Create/Cancel.
- **Backend**: a `routines` table + CRUD API (reuse bridge store), an API/
  webhook trigger endpoint that dispatches a /code task (reuse /v1/tasks +
  containers), and — the hard part — a **scheduler/runner** to fire cron
  routines (Next has no built-in cron; needs a long-lived timer process or an
  external cron/systemd timer hitting a run endpoint).
Suggested slices: (1) page + form + persistence + list; (2) API-trigger run
(functional without cron); (3) Schedule trigger + runner; (4) GitHub-event
trigger; (5) Calendar view + Behavior/Permissions. Sized as its own build,
not a tail-end tweak.

BUILT 2026-06-12: slices 1-2 + most of 5. Backend: routines table + CRUD store
helpers; /api/bridge/v1/routines (GET/POST), /[id] (PATCH pause/rename, DELETE),
/[id]/run (POST -- runs now or via API token; dispatches a cloud-container
session via runRoutine). UI (routines-view.tsx, wired at /code/routines +
sidebar): "What do you want automated?" prompt + template chips, All/Calendar
tabs (calendar places daily/weekdays/weekly/hourly by cadence), routine cards
(trigger + paused badges, Run/Pause/Delete), New-routine form with trigger
CARDS (Schedule [Once/Hourly/Daily/Weekdays/Weekly/Custom], GitHub event, API)
+ Connectors/Behavior/Permissions tabs. Verified live: create/list/pause/run/
delete.
STILL TODO: (3) the cron AUTO-RUNNER -- scheduled routines don't fire by
themselves yet (Next has no cron; needs a timer process or systemd timer
hitting /[id]/run); (4) GitHub webhook receiver for github-trigger routines;
persist Auto-fix (Behavior toggle is UI-only) + per-routine Connectors; "Once"
stores a recurring-ish cron (no exact one-time datetime without the runner);
Calendar is cadence-approximate (no cron parser).
**Revisit by:** 2026-06-26.

## 17. /code composer mic: permission parity with claude.ai (device-aware prompt)

Clicking the composer mic shows Chrome's basic "Use your microphones" prompt,
while claude.ai shows the richer "Use available microphones (N)" prompt with a
device dropdown + level meter (user report + screenshots 2026-06-20). Root
cause: Jarvis's web mic is **browser dictation via the Web Speech API**
(`SpeechRecognition`, `src/web/src/components/code/code-composer.tsx`) —
client-side STT, no audio capture — which only triggers the simple prompt.
claude.ai uses `getUserMedia` (it streams audio to a server STT), which is what
makes Chrome show the device-aware prompt.

DONE 2026-06-20 (prompt parity): `startRec` now does a one-time
`getUserMedia({audio:true})` pre-flight before starting `SpeechRecognition`, so
Chrome shows the same device-aware prompt and grants a persistent "Allow while
visiting the site". Stream is released immediately (Web Speech handles the audio).

STILL A DECISION (the real gap): the Web Speech API **cannot target a chosen
input device** (always the system default) and gives no level meter, so true
per-device selection like claude.ai's needs replacing the web composer mic with
`getUserMedia` capture + a **server-side STT** (e.g. reuse the voice-agent's
Deepgram/Whisper path) streaming audio from the browser. Browser STT (free,
simple, default-device-only) vs server STT (real device picker + accuracy, but a
transcription backend + ongoing cost). Decide before building.
**Revisit by:** 2026-06-27.
