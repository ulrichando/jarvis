# RFC-001: Reorganize `src/voice-agent/` into a properly-structured `voice/` package

- **Status:** accepted (Alternative D — Stages A + B only; Stage C deferred indefinitely)
- **Author:** `[ARCH]`
- **Date:** 2026-05-05
- **Reviewers:** `[ORCH]`, `[REVIEWER]`, `[INFRA]`, `[SEC]` (path-sensitive surfaces), `[QA]` (test imports)
- **Related:** F-arch-006 (this finding), W-007 (work item)

---

## Summary

Reorganize the voice service from `src/voice-agent/` (a flat directory of 32 top-level Python files mixed with three sub-packages) into `src/voice/` with a properly-structured package layout: `tools/`, `resilience/`, `sanitizers/`, `taps/`, `pipeline/`, `tts/`, plus the existing `specialists/`, `supervisor_graph/`, and `blackboard/`. The work is split into three stages (A, B, C) with explicit go/no-go gates so each stage's blast radius is bounded and reversible. **No stage runs without `[ORCH]` confirming the gate.**

## Motivation

User feedback 2026-05-05: *"the structure of voice-agent isn't even good — why not just name it voice, and in the voice folder have a folder for tools, structure it properly and name things professionally."*

The complaint is correct. A snapshot of the current top-level:

```
src/voice-agent/
├── 32 *.py files (mixed concerns: tools, resilience, sanitizers, taps, pipeline, TTS, entrypoints)
├── blackboard/         (well-named package)
├── specialists/        (well-named package)
├── supervisor_graph/   (well-named package)
└── tests/
```

Concrete problems:

- **Naming inconsistency.** Three sub-packages already follow a clean noun-namespace convention (`specialists/`, `blackboard/`, `supervisor_graph/`). The 32 top-level files violate it. They mix tool implementations (`jarvis_browser*.py`), resilience primitives (`circuit_breaker.py`, `watchdog.py`, `reconnect_ladder.py`), output sanitizers (`dsml_sanitizer.py`, `pycall_sanitizer.py`, `tool_name_sanitizer.py`, `handoff_text_suppressor.py`, `deepseek_roundtrip.py`), screen/audio taps (`acoustic_tap.py`, `vision_tap.py`), pipeline routing (`turn_router.py`, `turn_graph.py`, `turn_telemetry.py`, `dispatching_llm.py`, `dispatching_tts.py`), TTS plugins (`edge_tts_plugin.py`, `canned_phrases.py`), and the two service entrypoints (`jarvis_agent.py`, `jarvis_voice_client.py`).
- **Redundant `jarvis_` prefix.** Inside the voice package every file is jarvis-related; the prefix is dead namespacing. `jarvis_browser` becomes `tools/browser`; `jarvis_computer_use` becomes `tools/computer_use`. Reads cleanly.
- **`voice-agent` overloads two conventions.** The dash-form (`voice-agent`) is service-naming style (matches systemd `jarvis-voice-agent.service`); a Python package directory should be `voice_agent` or `voice`. Today nothing imports it as a package — every file uses `sys.path.insert(0, ...)` to make sibling imports work, which is a workaround for the dash. Renaming to `voice/` lets it be a real package.
- **Search and onboarding cost.** A new contributor sees 32 ungrouped files and has to read each docstring to understand structure. Five minutes of "what does this file do?" per file is real cost.

## Goals

- Make the top-level voice tree a properly-structured Python package with one logical concern per sub-folder.
- Drop the redundant `jarvis_` prefix on files inside the package.
- Rename the directory to `voice/` (matching the user's request and the natural Python-package convention).
- Do all of the above **without invalidating the in-progress 24h soak** for the Session 0 fixes (started 2026-05-05 14:27 UTC), **without breaking the four active worktrees**, and **without exceeding the 200-LOC / 3-file Charter cap on any single patch** beyond what an RFC explicitly authorizes.

## Non-goals

- Not changing any module's external behavior. This is a pure structural refactor.
- Not introducing new tools, providers, or features.
- Not renaming or restructuring `specialists/`, `supervisor_graph/`, or `blackboard/` — they are already correctly organized.
- Not renaming the systemd units (`jarvis-voice-agent.service`, `jarvis-voice-client.service`). They are public service names with downstream dependencies (Tauri tray, journalctl muscle memory). Internal Python paths are private; service names are not.
- Not introducing test churn beyond what import updates require.

## Proposal

### Target layout (Stage B + C complete)

```
src/voice/                          ← was src/voice-agent/
├── __init__.py                     ← new, exports stable surface
├── agent.py                        ← was jarvis_agent.py (entrypoint #1)
├── client.py                       ← was jarvis_voice_client.py (entrypoint #2)
├── tools/
│   ├── __init__.py
│   ├── browser.py                  ← was jarvis_browser.py
│   ├── browser_ext.py              ← was jarvis_browser_ext.py
│   ├── browser_v2.py               ← was jarvis_browser_v2.py
│   ├── code_reviewer.py            ← was jarvis_code_reviewer.py
│   ├── computer_use.py             ← was jarvis_computer_use.py
│   ├── github.py                   ← was jarvis_github.py
│   ├── log_analyzer.py             ← was jarvis_log_analyzer.py
│   ├── memory.py                   ← was jarvis_memory.py
│   ├── memory_recall.py            ← was jarvis_memory_recall.py
│   └── validator.py                ← was jarvis_validator.py
├── resilience/
│   ├── __init__.py
│   ├── circuit_breaker.py          ← unchanged
│   ├── llm_idle_timeout.py         ← unchanged
│   ├── reconnect_ladder.py         ← unchanged
│   ├── track_guard.py              ← was livekit_track_guard.py
│   └── watchdog.py                 ← unchanged
├── sanitizers/
│   ├── __init__.py
│   ├── deepseek_roundtrip.py       ← unchanged
│   ├── dsml.py                     ← was dsml_sanitizer.py
│   ├── handoff_text.py             ← was handoff_text_suppressor.py
│   ├── pycall.py                   ← was pycall_sanitizer.py
│   └── tool_name.py                ← was tool_name_sanitizer.py
├── taps/
│   ├── __init__.py
│   ├── acoustic.py                 ← was acoustic_tap.py
│   └── vision.py                   ← was vision_tap.py
├── pipeline/
│   ├── __init__.py
│   ├── dispatching_llm.py          ← unchanged
│   ├── dispatching_tts.py          ← unchanged
│   ├── turn_graph.py               ← unchanged
│   ├── turn_router.py              ← unchanged
│   └── turn_telemetry.py           ← unchanged
├── tts/
│   ├── __init__.py
│   ├── canned_phrases.py           ← unchanged
│   └── edge_tts_plugin.py          ← unchanged
├── confab_detector.py              ← stays at top (cross-cutting; used by agent.py + tests)
├── blackboard/                     ← unchanged
├── specialists/                    ← unchanged
├── supervisor_graph/               ← unchanged
└── tests/                          ← unchanged location; imports updated
```

### Staging

Three stages, each with an explicit go/no-go gate. **No stage runs until `[ORCH]` records the user's go-ahead in `03-STATE.md` Decisions ledger.**

#### Stage A: introduce `tools/` subfolder

- **Scope:** move the 10 `jarvis_*.py` tool implementations into `src/voice-agent/tools/` and strip the prefix. Keep `src/voice-agent/` directory name (no rename in Stage A).
- **Files moved (10):** browser, browser_ext, browser_v2, code_reviewer, computer_use, github, log_analyzer, memory, memory_recall, validator.
- **New file:** `src/voice-agent/tools/__init__.py` — re-exports public symbols so `from tools.browser import browser_task` is the canonical import.
- **Import sites to update:** ~30 across 25 files (3 in `jarvis_agent.py`, 7 in `specialists/*`, 20 in `tests/*`).
- **Backward-compat shim:** for one session, `src/voice-agent/jarvis_browser.py` etc. become 1-line `from tools.browser import *` wrappers, so any path I missed still resolves. Removed in a follow-up Stage A.2 cleanup once the test suite passes clean for one full session.
- **Restart needed:** yes, `jarvis-voice-agent.service` (picks up new module layout). One restart, mid-session.
- **Soak impact:** **resets the soak window** (Goal 1, 3, 6 measurement clock). The user must explicitly accept the cost before Stage A runs. Alternatively: defer Stage A until after T+24h (~2026-05-06 14:27 UTC).
- **Patch budget:** ~250 LOC across ~35 files (10 moves + 25 import updates). Exceeds the 200-LOC cap; this RFC is the authorization.
- **Tests:** existing test suite IS the regression test. Acceptance: full suite pre-Stage-A 690/692 must equal post-Stage-A 690/692, with the same 2 skips. Any new failure rolls back the stage entirely.
- **Rollback:** `git revert` the stage's commit. The shim files keep old import paths working, so the revert is one operation.

#### Stage B: introduce remaining sub-packages (`resilience/`, `sanitizers/`, `taps/`, `pipeline/`, `tts/`)

- **Scope:** move the remaining 20 top-level files into their concern-named sub-packages per the target layout.
- **Files moved (20):** see target layout above.
- **Import sites to update:** ~150 across the codebase (every `import circuit_breaker`, `import watchdog`, `import dsml_sanitizer`, etc., plus tests).
- **Same shim approach:** old top-level files become 1-line re-export wrappers. Removed in Stage B.2 once stable.
- **Patch budget:** ~600 LOC across ~80 files. Far over the cap. Authorized by this RFC; executed across **two sessions** if necessary, one sub-package at a time (resilience first, then sanitizers, then the rest), each with its own restart + suite-pass gate.
- **Restart needed:** one per sub-package landing. Soak impact: same as Stage A — resets the window each restart.

#### Stage C: rename `src/voice-agent/` → `src/voice/`

- **Scope:** the actual directory rename. Touches:
  - All `src/voice-agent/` references in repo (65 today, ~30 docs / 20 source / 10 systemd-related / 5 misc).
  - 7 systemd unit files.
  - 4 active worktrees — must be merged or rebased BEFORE Stage C, or every future merge produces conflicts.
  - The `src/voice-agent/.env` file path (referenced by `EnvironmentFile=` directives).
  - Internal `sys.path.insert(...)` lines in tests + scripts that hardcode the path.
- **Restart needed:** ALL voice services. systemd unit files must be reloaded (`systemctl --user daemon-reload`) and the units restarted in dependency order.
- **Patch budget:** ~150 LOC across ~70 files (mostly path-string changes). Authorized by this RFC.
- **Worktree pre-condition:** all 4 worktrees (`kimi-supreme`, `news-widget`, `screen-watching`, `voice-quality`) must be either (a) merged into the active branch and closed, OR (b) explicitly accepted as "will rebase manually after Stage C." User decides.
- **Rollback:** `git revert` plus `systemctl daemon-reload` plus restart. Bounded but more involved than Stage A or B.

### Stable surface (`src/voice/__init__.py`, post-Stage-C)

The new top-level `__init__.py` becomes the place external callers (none today; this is a service, not a library) would enter. For now it stays mostly empty — explicit-imports-only — so we don't accidentally promote anything to a stable API before we mean to.

### Observability

- Every shim file logs at `DEBUG` level when imported via the old path, so live telemetry shows when the deprecated import path is hit. After 7 days of zero such hits, the shim is removed.
- Voice-agent startup log includes a `[layout] voice/ package loaded — version <git-sha>` line at INFO so a journalctl reader knows which layout is in flight.

## Alternatives considered

### Alternative A: Do nothing, keep flat layout

- How it would work: Leave `src/voice-agent/` as-is. Document the implicit grouping in a CONTRIBUTING.md.
- Why we did not choose it: The user explicitly requested the reorganization. Documentation without enforcement decays — the next 10 files added would land at the top level too.

### Alternative B: One-shot rewrite (single commit)

- How it would work: One git commit moves all 30+ files, renames the directory, updates every reference, restarts every service.
- Why we did not choose it: Charter §3 Phase 5 patch-size cap (200 LOC / 3 files); a one-shot violates it 10x without any of the gating an RFC provides. Also creates a single huge merge conflict for every active worktree; the user has 4 worktrees today.

### Alternative C: Just rename the directory (skip internal reorganization)

- How it would work: `src/voice-agent/` → `src/voice/`. Leave the 32 flat files alone. Update systemd units + paths.
- Why we did not choose it: Half-fixes the user's complaint. The "structure it properly" part of the request is the bigger maintainability win; renaming alone is cosmetic.

### Alternative D: Reorganize internally but keep `voice-agent` directory name

- How it would work: Stages A + B only. Skip Stage C.
- Why this is a real option: It captures 80% of the maintainability win at 20% of the risk. The directory name is the most disruptive change because it touches systemd, docs, and worktrees. **Recommend this as the default unless the user wants the rename specifically.**

## Trade-offs

- **Costs across all stages:** ~3 sessions of focused work. ~1000 LOC of mechanical rename diffs. Some mid-stage soak-window resets.
- **Future-option costs:** the new layout commits us to those concern names. If we later want to move e.g. `resilience/circuit_breaker.py` to a shared library, we have one more rename to do.
- **One-time cost we avoid:** every new contributor's "what's in this directory?" scanning cost.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Missed import path produces import error at agent startup | medium | high | Backward-compat shim files (1-line re-exports) for one full session; structured logging to detect deprecated-path use. |
| Worktree merge conflicts | high (Stage C only) | medium | Stage C blocked on user decision about worktrees. Merge or accept-conflict, in writing. |
| Soak-window invalidation on each restart | high | medium | Each stage explicitly opt-in. Default recommendation: Stage A waits until after T+24h soak completes (~2026-05-06 14:27 UTC). |
| systemd unit files become inconsistent with Python layout | low (Stage A/B), high (Stage C) | high | Stage C ships unit-file edits + Python edits in the same commit. Atomic — daemon-reload + restart in unit dependency order. Documented rollback. |
| Test order-dependence resurfaces with new module-load order | low | medium | W-003 (Session 1) hardened the order-dependent flakes. New layout exercises a fresh module-load order; full suite must re-pass. |
| External callers (Tauri tray reading `~/.jarvis/voice-model`, etc.) break | low | medium | All external file/socket paths under `~/.jarvis/` are unchanged. Service names unchanged. Only Python module paths change. |
| User decides mid-stage that they don't want it after all | low | low | Each stage is one commit; revert is one operation. |

## Security considerations

- **Threat model:** unchanged. No new network surfaces, no new file-system writes, no prompt-construction changes. Pure source-file rename.
- **Secrets:** no new secrets introduced or moved. `src/voice-agent/.env` remains the EnvironmentFile until Stage C, then becomes `src/voice/.env`. The keys it contains do not change.
- **Sudoers / root:** the `/etc/sudoers.d/jarvis` rule (per memory `project_jarvis_root.md`) does not reference any of the renamed paths. No sudoers changes needed.
- **Blast radius if compromised:** unchanged from today's voice-agent.

`[SEC]` consulted; no concerns.

## Performance considerations

- Module-load time: trivially equivalent. Python has no measurable difference between flat-and-deep import paths.
- Memory: identical.
- No model / API changes.

## Testing strategy

- **Unit tests:** existing 690/692 must remain green throughout. Each stage's PR runs the full suite pre-merge.
- **Integration:** voice-agent + voice-client live restart after each stage; manual smoke test (one user-spoken turn, see it return audio cleanly) before claiming the stage done.
- **Soak-resume:** after each stage's restart, the soak clock for Goals 1/3/6 resets. Documented in `03-STATE.md` Acceptance Gate.
- **Regression:** no new test infra needed. The grep-the-log validation gates from the existing acceptance criteria continue to work.

## Rollout plan

| Stage | Action | Pre-condition | Post-condition |
|---|---|---|---|
| A | tools/ subfolder; strip prefix; shim wrappers | User approves Stage A; user accepts soak reset OR waits until T+24h | All 10 tool files moved; full suite green; one restart done; layout logged. |
| A.2 | remove backward-compat shims | 7 days of zero deprecated-path log hits | Shim files deleted. |
| B | resilience/ + sanitizers/ + taps/ + pipeline/ + tts/ | Stage A.2 done; user approves Stage B | Remaining 20 files moved; full suite green. |
| B.2 | remove Stage B shims | 7 days clean | Shim files deleted. |
| C | rename `voice-agent/` → `voice/` | Stage B.2 done; all 4 worktrees merged-or-resolved; user approves Stage C; systemd unit edits queued | Directory renamed; systemd units updated atomically; full suite green; full smoke test green. |
| Rollback (any stage) | `git revert <stage commit>`; if Stage C, `systemctl --user daemon-reload` + restart all voice units | — | Pre-stage state restored. |

## Open questions

- **Q1:** Does the user want all three stages, or is Alternative D (Stages A + B only, keep `voice-agent/` name) acceptable? The directory rename is the most disruptive piece; the value-per-risk on Stage C is the lowest of the three.
- **Q2:** Stage A timing — do we run it now (resets soak; we re-soak from this point) OR wait until T+24h soak completes (~2026-05-06 14:27 UTC)? Default recommendation: **wait**, so the breaker / Kimi / recall fixes get a clean soak signal first.
- **Q3:** For Stage C: are the 4 active worktrees ready to merge, or will they be discarded, or will the user accept manual rebase-after-rename? Need this answer before Stage C can be scheduled.
- **Q4:** Confab_detector.py is currently top-level and has no obvious sub-package home (it's used by `agent.py` and tests, sits cross-cutting). Leave at top level, or fold into `pipeline/`? Default: leave at top level.

---

## Decision log

| Date | Decision | By |
|---|---|---|
| 2026-05-05 | RFC drafted | `[ARCH]` |
| 2026-05-05 | **Accepted (Alternative D).** Stages A + B execute; Stage C (directory rename `voice-agent` → `voice`) **deferred indefinitely** — not scheduled. Stage A timing: **after the Session 0 soak completes** (~2026-05-06 14:27 UTC, T+24h from the post-fix restart). Confab_detector stays top-level. | user (Ulrich) on `[ORCH]`'s recommendation |
