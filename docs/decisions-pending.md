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

## 11. ROTATE: LangSmith key + Postgres password; re-scrub history before public flip

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
connector PAT (token scrubbed from .git/config after), optional
.jarvis/setup.sh, pre-seeded workspace trust (CLAUDE_CONFIG_DIR
/jarvis-config), then the CLI source bind-mounted RO + vendored bun runs
`cli.tsx --print --sdk-url <web>/v1/code/sessions/{id}` — the same worker
endpoints as bridge children, so SSE/permissions/transcripts are unchanged.
Init steps stream as status session_events.
MVP tradeoffs (next phases): --network=host (web binds 127.0.0.1; the
egress-proxy phase replaces this — until then container isolation is
filesystem/process, NOT network), no branch/PR push-back yet (clone cred is
scrubbed; push needs the scoped-credential flow), no setup snapshot
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
- **Add files or photos** (plus menu): needs image/file plumbing — composer
  must thread sessionId + read files→base64, an extended messages route to
  accept SDK image content blocks, and provider-render verification (the
  text-only fallback LLM rungs won't see images). Threading through the
  shared composer also collides with the in-flight container work; do it
  once that settles.
- **Sidebar menu (2026-06-12 update): Pin, Copy link, and relative time
  ("2m ago") are now DONE** — `pinned` column + pinned-first sort, PATCH
  {pinned}, `/code?s=<id>` deep link for Copy link, time on each row (hidden
  on hover for the kebab). Menu order/shortcuts (P/R/C/A/D) mirror claude.ai.
  STILL stubbed (each needs state JARVIS lacks): **Mark as read** (read/unread
  column), **Move to group** (group_id + grouped list), **Share** (per-session
  visibility model — the page Share modal is still a stub), **Open in** (no
  teleport/editor-open equivalent self-hosted).
**Revisit by:** 2026-06-26.
