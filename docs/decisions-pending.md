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
