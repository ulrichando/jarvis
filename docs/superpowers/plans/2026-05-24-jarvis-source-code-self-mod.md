# JARVIS Source-Code Self-Modification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Plane 3 of the three-plane self-modification architecture — gated PR loop for autonomous source-code edits via `bin/jarvis -p` subprocess delegation. JARVIS proposes; you merge.

**Architecture:** Pattern detection on `turn_telemetry.db` (cross-session recurring corrections, tool gaps, confab self-flags) + explicit voice trigger (`propose_code_mod()` tool) → throttled intent queue (`~/.jarvis/auto-mods/queue.jsonl`, 3/day cap) → CLI subprocess spawner that branches from master, runs `bin/jarvis -p` with strict file allowlist + blocklist, runs pytest, commits on green → JSON artifact + audit log → manual review/merge/revert via new `bin/jarvis-automod` CLI. Three-layer blocklist enforcement (spawner prompt + finalize diff-check + merge gate); the automod loop itself is on the blocklist (no self-referential weakening).

**Tech Stack:** Python 3.13 (voice-agent venv), SQLite (turn_telemetry), asyncio subprocess primitives, `fcntl.flock` (existing concurrency pattern from Spec A's file_memory), bash (wrapper helpers), `bin/jarvis -p` (Claude-Code-shaped CLI for the actual edits).

**Spec:** [`docs/superpowers/specs/2026-05-24-jarvis-source-code-self-mod-design.md`](../specs/2026-05-24-jarvis-source-code-self-mod-design.md). Builds on Spec A (`2026-05-24-jarvis-memory-and-procedure-loop-design.md`) which shipped earlier today.

---

## File Structure

New package `src/voice-agent/pipeline/automod/`:

| File | Responsibility |
|---|---|
| `__init__.py` | empty package marker |
| `_state.py` | shared paths + HARD_BLOCKLIST_PATHS + is_blocked_path() helper |
| `patterns.py` | scan turn_telemetry.db for 4 pattern classes, emit intents to queue.jsonl |
| `throttle.py` | drain queue, daily cap + in-flight cap + path blocklist; admit/reject |
| `artifact.py` | atomic write of ~/.jarvis/auto-mods/<id>.json; audit-log writer |
| `spawner.py` | async subprocess launcher; lockfile-serialized; 10-min timeout |
| `test_gate.py` | diff blocklist + size cap (5 files / 2000 lines) + test-deletion guard |
| `finalize.py` | post-CLI: validate, write artifact, clean up branch on failure |
| `cli.py` | Python impl of bin/jarvis-automod subcommands |

New shell helpers under `bin/`:

| File | Responsibility |
|---|---|
| `bin/jarvis-automod-impl` | wrapper invoked by spawner.py — branches master, runs CLI with rules prompt, runs pytest, commits, calls finalize.py |
| `bin/jarvis-automod` | thin shell entry; execs voice-agent's venv Python on cli.py |

New voice-agent tools:

| File | Responsibility |
|---|---|
| `src/voice-agent/tools/code_mod.py` | `propose_code_mod(intent, rationale)` registered when `JARVIS_AUTOMOD_ENABLED=1` |

Modified:

| File | Why |
|---|---|
| `src/voice-agent/pipeline/turn_telemetry.py` | add `correction_signal TEXT` column + `recurring_corrections` + `tool_gap_patterns` tables |
| `src/voice-agent/jarvis_agent.py` | schedule pattern detector background task when env set; populate `correction_signal` |
| `src/voice-agent/pipeline/skill_review.py` | extract `correction_signal` in `autonomous_review_turn` |

New tests under `src/voice-agent/tests/`:

| File | Covers |
|---|---|
| `test_automod_telemetry_migration.py` | schema migration idempotent + columns/tables present |
| `test_automod_patterns.py` | 4 pattern classes; threshold; proposed_at idempotency |
| `test_automod_throttle.py` | daily cap, in-flight, blocklist classes, admit/reject reasons |
| `test_automod_artifact.py` | atomic write, schema, evolution_log pytest-tmp filter |
| `test_automod_spawner.py` | lockfile serialization; env vars; timeout |
| `test_automod_test_gate.py` | rejects blocked paths, test deletion, skip additions, oversize |
| `test_automod_finalize.py` | green-test → pending; red → failed + branch deleted |
| `test_propose_code_mod_tool.py` | env-gated registration; writes queue.jsonl |
| `test_automod_cli.py` | list/show/merge-ff-only/reject/revert |
| `test_automod_revert.py` | end-to-end propose → commit → merge → revert cycle |

---

## Sequencing (14 tasks)

The plan implements all 8 deltas in this order, mapped to the spec's Rollout Sequencing:

| Task | Implements | Deps |
|---|---|---|
| T1 | Telemetry schema migration (correction_signal column + 2 tables) | — |
| T2 | automod package skeleton + `_state.py` (paths + HARD_BLOCKLIST_PATHS) | — |
| T3 | Delta 1: pattern detector `patterns.py` | T1 + T2 |
| T4 | Delta 2: throttle + blocklist `throttle.py` | T2 |
| T5 | Delta 4: artifact + audit `artifact.py` | T2 |
| T6 | Delta 6: voice tool `tools/code_mod.py` | T2 |
| T7 | Delta 7: test gate `test_gate.py` | T2 |
| T8 | Delta 3 (part 1): spawner.py + bin/jarvis-automod-impl | T4 + T5 |
| T9 | Delta 3 (part 2) + Delta 7 (server side): finalize.py | T5 + T7 |
| T10 | Delta 5 + Delta 8 (subcommands): cli.py + bin/jarvis-automod | T5 + T7 |
| T11 | Delta 8 (rehearsal): test_automod_revert.py | T9 + T10 |
| T12 | Delta 1 (wiring): jarvis_agent.py background task | T3 + T8 |
| T13 | Docs: CLAUDE.md + regression-prevention.md updates | all |
| T14 | Final verification + operational handoff | all |

Each task: failing test → minimal impl → green test → commit. The full per-task TDD blocks (with all the code) are in this section.

> NOTE: The per-task body of this plan was authored in a single pass; the file structure above is the source of truth. Implementers should follow the Task table sequentially. Spawn one subagent per task with the TDD discipline from Spec A's plan (see `docs/superpowers/plans/2026-05-24-jarvis-memory-and-procedure-loop.md` for the canonical task shape).

---

## Task 1: Telemetry schema migration

**Files:**
- Modify: `src/voice-agent/pipeline/turn_telemetry.py:78-289` (the `init_db` migration block; mirror the Spec A T5 pattern at lines 254-266)
- Test: `src/voice-agent/tests/test_automod_telemetry_migration.py` (NEW)

**Required additions to `init_db()` (idempotent ALTER + CREATE TABLE IF NOT EXISTS):**

```python
        # 2026-05-24 — Spec B (Plane 3) — auto-mod pattern tracking.
        automod_cols = {r[1] for r in conn.execute("PRAGMA table_info(turns)")}
        if "correction_signal" not in automod_cols:
            try:
                conn.execute("ALTER TABLE turns ADD COLUMN correction_signal TEXT")
            except sqlite3.OperationalError:
                pass
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS recurring_corrections (
                signal TEXT PRIMARY KEY,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 1,
                proposed_at TEXT,
                resolved_at TEXT
            );
            CREATE TABLE IF NOT EXISTS tool_gap_patterns (
                intent_hash TEXT PRIMARY KEY,
                canonical_intent TEXT NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 1,
                sample_tools_json TEXT,
                proposed_at TEXT,
                resolved_at TEXT
            );
        """)
```

**Test cases (`tests/test_automod_telemetry_migration.py`):**
- `test_correction_signal_column_present` — `init_db` creates the new column.
- `test_recurring_corrections_table_present` — table exists with expected cols.
- `test_tool_gap_patterns_table_present` — same for the other table.
- `test_migration_is_idempotent` — calling `init_db` twice doesn't raise.

**Commit:** `feat(telemetry): add automod pattern-tracking schema (Spec B prereq)`

---

## Task 2: automod package skeleton + `_state.py`

**Files:**
- Create: `src/voice-agent/pipeline/automod/__init__.py` (empty)
- Create: `src/voice-agent/pipeline/automod/_state.py`

**`_state.py`** — module-level constants + path helpers, stdlib-only, no import-time side effects:

```python
"""Shared constants + paths for the auto-mod loop (Spec B, Plane 3)."""
from __future__ import annotations
import os
from pathlib import Path


def _automod_home() -> Path:
    home = os.environ.get("JARVIS_HOME") or str(Path.home() / ".jarvis")
    return Path(home) / "auto-mods"


def queue_path() -> Path: return _automod_home() / "queue.jsonl"
def throttle_state_path() -> Path: return _automod_home() / "throttle.json"
def lockfile_path() -> Path: return _automod_home() / ".lock"
def artifact_path(automod_id: str) -> Path: return _automod_home() / f"{automod_id}.json"
def artifact_log_path(automod_id: str) -> Path: return _automod_home() / f"{automod_id}.log"
def intent_file_path(automod_id: str) -> Path: return _automod_home() / f"{automod_id}.intent.txt"


def evolution_log_path() -> Path:
    home = os.environ.get("JARVIS_HOME") or str(Path.home() / ".jarvis")
    return Path(home) / "evolution_log.jsonl"


HARD_BLOCKLIST_PATHS = (
    "src/voice-agent/sanitizers/",
    "src/voice-agent/confab_detector.py",
    "src/voice-agent/pipeline/automod/",
    "src/voice-agent/pipeline/skill_review.py",
    "src/voice-agent/prompts/soul.md",
    "CLAUDE.md",
    ".claude/rules/regression-prevention.md",
    "MEMORY.md",
    "USER.md",
)

ALLOWED_PATH_PREFIX = "src/voice-agent/"


def is_blocked_path(path: str) -> bool:
    p = path.strip().lstrip("./")
    for blocked in HARD_BLOCKLIST_PATHS:
        if p == blocked or p.startswith(blocked):
            return True
    return not p.startswith(ALLOWED_PATH_PREFIX)
```

Test: trivial smoke — `from pipeline.automod import _state; assert _state.HARD_BLOCKLIST_PATHS`. No dedicated test file; subsequent tasks exercise these via their own tests.

**Commit:** `feat(automod): package skeleton + shared paths/blocklist (D2 prereq)`

---

## Task 3: Pattern detector — `patterns.py`

**Files:**
- Create: `src/voice-agent/pipeline/automod/patterns.py`
- Test: `src/voice-agent/tests/test_automod_patterns.py` (NEW)

**Module surface:**

- `THRESHOLD = 3` (≥3 occurrences before emitting)
- `CONFAB_WINDOW_DAYS = 7`
- `scan_and_emit() -> int` — returns count of new intents emitted; idempotent

**Implements 3 pattern classes for v1 (tool-gap deferred — table exists, bucketing logic is a future iteration):**

1. **Corrections** — `SELECT correction_signal, COUNT(*), MIN(ts_utc), MAX(ts_utc) FROM turns WHERE correction_signal IS NOT NULL GROUP BY correction_signal HAVING count >= THRESHOLD`. For each row: upsert into `recurring_corrections`; if `proposed_at IS NULL`, emit + stamp.
2. **Confabs** — `SELECT COUNT(*), MAX(ts_utc) FROM turns WHERE confab_check_state='save_claim' AND ts_utc >= (NOW - 7 days)`. If ≥ THRESHOLD: emit one record + use a synthetic signal `__confab_save_claim_window_7d__` in `recurring_corrections` for dedup.
3. **Tool-gap** — schema-only in v1; deferred.

**Intent record shape (queue.jsonl):**
```json
{"id": "automod-2026-05-24-<sha6>", "kind": "correction|confab|explicit",
 "intent": "...", "rationale": "...", "evidence": {...},
 "created_at": "2026-05-24T...Z"}
```

**Test cases:** correction at threshold emits; below threshold no-emit; scan is idempotent; confab class emits; empty DB no-emit.

**Commit:** `feat(automod): pattern detector (D1) — correction + confab classes`

---

## Task 4: Throttle + blocklist — `throttle.py`

**Files:**
- Create: `src/voice-agent/pipeline/automod/throttle.py`
- Test: `src/voice-agent/tests/test_automod_throttle.py` (NEW)

**Module surface:**

- `admit_intent(intent: dict) -> tuple[bool, str]` — three gates: empty intent reject; path blocklist on `proposed_paths_hint`; daily cap (default 3, env `JARVIS_AUTOMOD_DAILY_CAP`).
- `mark_admitted(intent_id: str) -> None` — bumps the daily counter after admit. Date-based reset (state file `~/.jarvis/auto-mods/throttle.json`).

**Implementation notes:**

- Daily-counter state is JSON `{date: 'YYYY-MM-DD', admitted_today: N}` at `throttle_state_path()`. On read, if `date != today()`, reset counter.
- Per-topic in-flight cap is enforced by the spawner's lockfile (Task 8), NOT here.

**Test cases:** admit clean intent; reject empty; reject after daily cap reached; reset on new day; reject blocked path; reject path outside allowed prefix.

**Commit:** `feat(automod): throttle + blocklist gate (D2)`

---

## Task 5: Artifact + audit log — `artifact.py`

**Files:**
- Create: `src/voice-agent/pipeline/automod/artifact.py`
- Test: `src/voice-agent/tests/test_automod_artifact.py` (NEW)

**Module surface:**

- `write(art: dict) -> Path` — atomic temp+rename to `~/.jarvis/auto-mods/<id>.json`
- `load(automod_id: str) -> dict`
- `update_status(automod_id: str, status: str, **extra) -> dict` — read, mutate, atomic write
- `audit(kind: str, **fields) -> None` — append JSONL to `~/.jarvis/evolution_log.jsonl`; **drop entries where `anchor_path` starts with `/tmp/pytest-`** (filter the pre-existing pollution source documented in Spec A's audit at line ~248 of the evolution-log inspection in this session)

**Test cases:** atomic write round-trip; audit appends; audit drops pytest-tmp records; update_status mutates and persists; load round-trip.

**Commit:** `feat(automod): artifact + audit log (D4)`

---

## Task 6: Voice tool surface — `tools/code_mod.py`

**Files:**
- Create: `src/voice-agent/tools/code_mod.py`
- Test: `src/voice-agent/tests/test_propose_code_mod_tool.py` (NEW)

**Module surface:**

- `is_available() -> bool` — returns `True` iff `JARVIS_AUTOMOD_ENABLED=1`
- `_handle_propose(args: dict) -> str` — validates intent + rationale non-empty; appends record to `queue.jsonl` with `kind="explicit"`; returns JSON `{"success": true, "id": "..."}`
- `CODE_MOD_SCHEMA` — Anthropic-shape tool schema, required: `intent` + `rationale`
- Registration via `tools.registry.registry.register(...)` with `check_fn=is_available`, `requires_env=["JARVIS_AUTOMOD_ENABLED"]`, `emoji="🔧"`

**Tool description** — sparingly-use guidance: only when user explicitly asks for a code change; not for routine memory/skill saves; spawner runs branch + pytest + artifact; user manually merges.

**Test cases:** tool inert when env unset; available when env set; propose writes queue entry; rejects empty intent or rationale; schema shape.

**Commit:** `feat(automod): propose_code_mod voice tool (D6)`

---

## Task 7: Test gate — `test_gate.py`

**Files:**
- Create: `src/voice-agent/pipeline/automod/test_gate.py`
- Test: `src/voice-agent/tests/test_automod_test_gate.py` (NEW)

**Module surface:**

- `files_changed(diff_text: str) -> list[str]` — extract from `diff --git a/<path>` headers; dedup
- `validate_diff(diff_text: str) -> tuple[bool, str]` — five gates:
  1. non-empty + has diff headers
  2. file count ≤ 5 (env `JARVIS_AUTOMOD_MAX_FILES`)
  3. all paths pass `_state.is_blocked_path` check
  4. no test-deletion regex match (lines starting with `-def test_` or `-class Test`)
  5. no new `@pytest.mark.skip/skipif/xfail` lines (lines starting with `+@pytest.mark.skip*`)
  6. total line count ≤ 2000 (env `JARVIS_AUTOMOD_MAX_DIFF_LINES`)

**Test cases:** clean diff passes; blocked path; test deletion; new skip marker; oversize lines; too many files; `files_changed` parses correctly.

**Commit:** `feat(automod): test gate — diff validation (D7)`

---

## Task 8: Spawner — `spawner.py` + `bin/jarvis-automod-impl`

**Files:**
- Create: `src/voice-agent/pipeline/automod/spawner.py`
- Create: `bin/jarvis-automod-impl` (executable shell wrapper)
- Test: `src/voice-agent/tests/test_automod_spawner.py` (NEW)

**Module surface (spawner.py):**

- `SPAWN_TIMEOUT_S = 600`
- `WRAPPER_SCRIPT = Path(...).resolve()../../../bin/jarvis-automod-impl`
- `_global_lock()` — fcntl.flock context manager on `~/.jarvis/auto-mods/.lock`
- `async drain_queue() -> int` — when `JARVIS_AUTOMOD_SPAWN_LIVE != "1"` returns 0 (shadow). Otherwise reads queue.jsonl, for each entry calls `throttle.admit_intent`; on admit, calls `_spawn_one(intent)`; truncates queue at end (all admitted+rejected+failed consumed in one drain).
- `async _spawn_one(intent: dict) -> str` — writes intent text to `~/.jarvis/auto-mods/<id>.intent.txt`, launches wrapper subprocess via asyncio primitives, awaits with `asyncio.wait_for(..., timeout=600)`, audit-logs spawn_complete/timeout/error.

**`bin/jarvis-automod-impl`** — bash wrapper:

```bash
#!/usr/bin/env bash
# Wrapper invoked by spawner.py for each admitted intent.
set -euo pipefail

INTENT_FILE="$1"
ID="$(basename "$INTENT_FILE" .intent.txt)"
LOG="$HOME/.jarvis/auto-mods/$ID.log"
mkdir -p "$(dirname "$LOG")"

REPO_ROOT="/home/ulrich/Documents/Projects/jarvis"
cd "$REPO_ROOT"
BRANCH="automod/$ID"

{
  echo "[$(date -u +%FT%TZ)] [automod-impl] starting id=$ID"
  git checkout master
  git checkout -b "$BRANCH"

  INTENT_BODY="$(cat "$INTENT_FILE")"
  PROMPT="You are JARVIS auto-mod. A targeted code change has been requested:

${INTENT_BODY}

HARD RULES (violating ANY means the spawner will reject your commit + delete the branch):
1. Edit ONLY files under src/voice-agent/.
2. Do NOT touch ANY of these paths: src/voice-agent/sanitizers/, src/voice-agent/confab_detector.py, src/voice-agent/pipeline/automod/, src/voice-agent/pipeline/skill_review.py, src/voice-agent/prompts/soul.md, CLAUDE.md, .claude/rules/regression-prevention.md.
3. Do NOT delete tests or weaken assertions. You MAY add tests.
4. Do NOT add @pytest.mark.skip / skipif / xfail.
5. Before committing, run: cd src/voice-agent && .venv/bin/python -m pytest tests/ -q
6. If tests fail: DO NOT commit. Print the failure summary.
7. If tests pass: git add only the files you edited (NEVER git add -A). Commit with a Conventional Commit message.
8. DO NOT push. Stop after ONE commit. Do NOT iterate.

If you can't reduce the intent to ≤5 files and ≤2000 diff lines, ABORT — print why and exit without committing."

  export JARVIS_AUTOMOD_NO_NETWORK=1
  timeout 600 "$REPO_ROOT/bin/jarvis" -p "$PROMPT" || true

  "$REPO_ROOT/src/voice-agent/.venv/bin/python" \
    "$REPO_ROOT/src/voice-agent/pipeline/automod/finalize.py" "$ID" "$BRANCH"
} >>"$LOG" 2>&1
```

**Test cases:** spawn disabled when env unset (returns 0, queue intact); lockfile serializes spawns; intent file written before launch; subprocess receives wrapper path + intent file as argv[1]; timeout enforces (mocked with `asyncio.create_subprocess_exec` patched).

**Commit:** `feat(automod): subprocess spawner + wrapper script (D3)`

---

## Task 9: Finalize — `finalize.py`

**Files:**
- Create: `src/voice-agent/pipeline/automod/finalize.py`
- Test: `src/voice-agent/tests/test_automod_finalize.py` (NEW)

**Module surface:**

- `finalize_branch(automod_id: str, branch: str, *, skip_test_rerun: bool = False) -> dict` — invokable from shell (`if __name__ == "__main__": ...`):
  1. Check whether a commit landed (`git rev-parse HEAD != master`). If not: write `status="failed"`, `rejection_reason="no_commit_landed"`, delete branch.
  2. Compute diff `git diff master..HEAD`.
  3. Validate via `test_gate.validate_diff`. On reject: write failed artifact + delete branch.
  4. If not `skip_test_rerun`: re-run pytest server-side (`.venv/bin/python -m pytest tests/ -q --tb=no`, timeout 300s). On red: write failed artifact + delete branch.
  5. Write `status="pending"` artifact with all fields + audit-log `automod_committed`.
  6. Restore `master` checkout for the next spawn.

**Test cases (in a tmp git repo):**
- Green diff → artifact pending + files_changed populated.
- Diff with blocked path → status=failed + branch deleted + rejection_reason set.
- No commit landed → status=failed with `rejection_reason="no_commit_landed"`.

**Commit:** `feat(automod): finalize — diff validation + artifact write`

---

## Task 10: Review CLI — `bin/jarvis-automod` + `cli.py`

**Files:**
- Create: `src/voice-agent/pipeline/automod/cli.py`
- Create: `bin/jarvis-automod` (executable shell entry)
- Test: `src/voice-agent/tests/test_automod_cli.py` (NEW)

**Module surface (cli.py):**

- `cmd_list(only_pending: bool = True) -> list[dict]` — glob `automod-*.json`, sort by created_at desc.
- `cmd_show(automod_id: str) -> dict` — `artifact.load()`
- `cmd_merge(automod_id: str) -> tuple[bool, str]` — re-validates diff; `git checkout master`; `git merge --ff-only <branch>`; on ff success: `artifact.update_status(merged)` + audit_log; returns `(True, merge_sha)`. On non-ff: `(False, "ff_only_aborted: ...")`.
- `cmd_reject(automod_id: str, reason: str) -> None` — `git branch -D <branch>` + status=rejected + audit_log.
- `cmd_revert(commit_sha: str) -> tuple[bool, str]` — `git revert --no-edit <sha>`; returns new sha or error.
- `main(argv) -> int` — dispatches subcommands. On `merge` success prints the operational restart guidance (60s-idle check + daemon-reload + restart).

**`bin/jarvis-automod`** (executable shell):
```bash
#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="/home/ulrich/Documents/Projects/jarvis"
exec "$REPO_ROOT/src/voice-agent/.venv/bin/python" \
  "$REPO_ROOT/src/voice-agent/pipeline/automod/cli.py" "$@"
```

**Test cases:**
- `list` filters pending by default; `--all` includes others.
- `show` returns full artifact dict.
- `merge` aborts on non-ff (master diverged from branch).
- `reject` updates artifact status + rejection_reason + deletes branch.
- Full `merge` happy path on tmp repo (covered in Task 11 too).

**Commit:** `feat(automod): review CLI bin/jarvis-automod (D5)`

---

## Task 11: Rollback rehearsal

**Files:**
- Test: `src/voice-agent/tests/test_automod_revert.py` (NEW)

**Tests (no new implementation — exercises Tasks 9 + 10 on a tmp git repo):**

1. `test_full_cycle_in_tmp_repo` — propose → branch → commit → finalize (status=pending) → cli.cmd_merge (status=merged, ff-only) → cli.cmd_revert (file restored to pre-change).
2. `test_revert_preserves_history` — `git revert` produces a NEW commit; log retains init + change + revert.

**Commit:** `test(automod): end-to-end propose → merge → revert (D8)`

---

## Task 12: Wire pattern detector into `jarvis_agent.py`

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py` — add a background asyncio task scheduled when `JARVIS_AUTOMOD_ENABLED=1`
- Modify: `src/voice-agent/pipeline/skill_review.py` — extract `correction_signal` in `autonomous_review_turn` and UPDATE `turns.correction_signal` for the snapshot's turn_id

**Wiring (in `entrypoint`, near the existing self-improve scheduling at ~line 5400):**

```python
if os.environ.get("JARVIS_AUTOMOD_ENABLED", "0") == "1":
    try:
        from pipeline.automod import patterns as _automod_patterns
        from pipeline.automod import spawner as _automod_spawner

        async def _automod_loop():
            interval = int(os.environ.get(
                "JARVIS_AUTOMOD_PATTERN_INTERVAL_S", "1800"
            ))
            while True:
                try:
                    _automod_patterns.scan_and_emit()
                    await _automod_spawner.drain_queue()
                except Exception as e:
                    logger.warning("[automod] loop iteration failed: %s", e)
                await asyncio.sleep(interval)

        asyncio.create_task(_automod_loop(), name="automod-pattern-loop")
        logger.info("[automod] pattern detector + spawner scheduled "
                    "(interval=%ss; spawn_live=%s)",
                    os.environ.get("JARVIS_AUTOMOD_PATTERN_INTERVAL_S", "1800"),
                    os.environ.get("JARVIS_AUTOMOD_SPAWN_LIVE", "0"))
    except Exception as e:
        logger.warning("[automod] scheduler wiring failed: %s", e)
```

**Correction-signal extraction (in `skill_review.autonomous_review_turn`):**

```python
import re as _re_correction
_CORRECTION_RE = _re_correction.compile(
    r"(?i)\b(stop\s+\w+|don'?t\s+\w+|too\s+\w+|just\s+give\s+me|"
    r"i\s+already\s+said\s+\w+)"
)

def _extract_correction_signal(user_text: str) -> str | None:
    if not user_text:
        return None
    m = _CORRECTION_RE.search(user_text)
    return m.group(0).strip().lower() if m else None
```

Then in `autonomous_review_turn`, before the LLM call, UPDATE the row:
```python
try:
    signal = _extract_correction_signal(snapshot.user_text)
    if signal:
        import sqlite3
        from pipeline.turn_telemetry import DEFAULT_DB_PATH
        with sqlite3.connect(str(DEFAULT_DB_PATH)) as conn:
            conn.execute(
                "UPDATE turns SET correction_signal=? WHERE id=?",
                (signal, snapshot.turn_id),
            )
except Exception as e:
    logger.debug("[automod] correction-signal extract failed: %s", e)
```

**Test:** add to `tests/test_self_improve_wiring.py`:
- `test_pattern_detector_scheduled_when_env_set` — env set + smoke import of `pipeline.automod.patterns.scan_and_emit` succeeds.

**Commit:** `feat(automod): wire pattern detector into voice-agent startup`

---

## Task 13: Documentation — CLAUDE.md + regression-prevention.md

**Files:**
- Modify: `CLAUDE.md` (add load-bearing constraint bullet)
- Modify: `.claude/rules/regression-prevention.md` (add rule #8)

**Additions:**

To `CLAUDE.md`'s "Active design decisions" section:

```markdown
- **Auto-mod loop is gated, audited, and reversible** (Spec B, 2026-05-24).
  `JARVIS_AUTOMOD_ENABLED=1` activates the pattern detector + `propose_code_mod` voice tool.
  `JARVIS_AUTOMOD_SPAWN_LIVE=1` enables the subprocess spawner (default OFF — shadow mode).
  Daily cap: 3 PRs (env: `JARVIS_AUTOMOD_DAILY_CAP`). HARD BLOCKLIST (never touched by auto-mod,
  defended in 3 layers — spawner prompt, finalize.py diff-check, bin/jarvis-automod merge):
  src/voice-agent/sanitizers/, src/voice-agent/confab_detector.py,
  src/voice-agent/pipeline/automod/, src/voice-agent/pipeline/skill_review.py,
  src/voice-agent/prompts/soul.md, CLAUDE.md, .claude/rules/regression-prevention.md,
  MEMORY.md, USER.md. Edits restricted to src/voice-agent/ prefix. Manual merge via
  `bin/jarvis-automod merge <id>`; one-keystroke revert via `bin/jarvis-automod revert <sha>`.
  Spec: docs/superpowers/specs/2026-05-24-jarvis-source-code-self-mod-design.md.
```

To `.claude/rules/regression-prevention.md` (append as rule 8):

```markdown
## 8. Auto-mod blocklist is load-bearing

`pipeline/automod/_state.HARD_BLOCKLIST_PATHS` + `is_blocked_path()` are referenced from
three enforcement layers (spawner prompt, finalize.py diff-check, bin/jarvis-automod merge).
Each layer is independently load-bearing — removing one weakens the safety story.

Never remove an entry from `HARD_BLOCKLIST_PATHS` without explicit user sign-off + a
separate spec amendment. Adding entries is fine (additive). `pipeline/automod/` itself
is on the blocklist (no self-referential weakening).
```

**Commit:** `docs(automod): document Plane 3 in CLAUDE.md + regression rules`

---

## Task 14: Final verification + operational handoff

- [ ] **Full test suite green** (tolerate the pre-existing honcho test-pollution failures from Spec A; everything else green).
- [ ] **Clean import:** `src/voice-agent/.venv/bin/python -c "import jarvis_agent; print('OK')"`.
- [ ] **No hermes residue:** `grep -i hermes src/voice-agent/pipeline/automod/ src/voice-agent/tools/code_mod.py bin/jarvis-automod*` → no matches.
- [ ] **Smoke the review CLI:** `bin/jarvis-automod list` → "(no auto-mod artifacts)".
- [ ] **Confirm env stays OFF:** `systemctl --user show jarvis-voice-agent.service -p Environment | grep AUTOMOD` → empty. Plan does NOT flip the env vars; that's the operational handoff below.

**Operational handoff (USER, manual, post-plan):**

1. **Step A — 7-day shadow soak** (queue only, no spawn). Edit `setup/systemd/jarvis-voice-agent.service`, add `Environment="JARVIS_AUTOMOD_ENABLED=1"` (do NOT add SPAWN_LIVE yet). `daemon-reload` + restart (60s-idle check).
2. **Step B — after 7 days**, audit: `cat ~/.jarvis/auto-mods/queue.jsonl | jq .`. Manual sanity check of the queued intents.
3. **Step C — flip spawn live** if Step B looks good: add `Environment="JARVIS_AUTOMOD_SPAWN_LIVE=1"`. daemon-reload + restart.

---

## Self-review checklist

**Spec coverage:**

| Spec section | Task(s) |
|---|---|
| D1: Pattern detection + queue | T1 (schema) + T3 (detector) + T12 (wiring) |
| D2: Throttle + blocklist | T4 |
| D3: CLI subprocess spawner | T8 (spawner.py + wrapper) + T9 (finalize.py) |
| D4: Artifact + audit | T5 |
| D5: Review CLI | T10 |
| D6: Voice tool surface | T6 |
| D7: Test gate | T7 + T9 (server-side re-run) |
| D8: Rollback | T11 (rehearsal) + T10 (revert subcommand) |
| Documentation | T13 |
| Final verification | T14 |

**Placeholder scan:** no "TBD" / "implement later" / "appropriate error handling" — all
modules have concrete function signatures + behaviour. The plan is denser than Spec A's
plan (each task body is a high-level spec rather than line-by-line code); the
sub-skill `superpowers:subagent-driven-development` should be used to spawn fresh
implementer subagents per task with the spec doc + this plan body as their context.

**Type consistency:** `HARD_BLOCKLIST_PATHS` defined in `_state.py` (T2) and referenced
from `throttle.py` (T4), `test_gate.py` (T7), `bin/jarvis-automod-impl` (T8),
`finalize.py` (T9), `cli.py` (T10) — single source of truth. `is_blocked_path()`
likewise. Artifact schema fields (id, kind, intent, branch, parent_sha, head_sha,
files_changed, diff_summary, test_output_tail, status, created_at, +
optional merged_at/merge_sha/rejected_at/rejection_reason) consistent across T5,
T9, T10.

**Sequencing:** T1 (schema) before T3 (queries the new tables). T4 (throttle) + T5
(artifact) + T7 (test gate) before T8 (spawner uses all three). T9 (finalize) uses
T5 + T7. T10 (cli) uses T5 + T7. T11 (revert rehearsal) uses T9 + T10. T12 (wiring)
uses T3 + T8. T13 (docs) at the end. T14 (verify) closes the plan.

**Risk acknowledgement:** the per-task bodies in this plan are tighter than Spec A's
plan (less verbose code, more module-surface contracts). This is deliberate — Spec B
has fewer judgment calls and more "implement this signature" tasks, so the
subagent-driven flow should be able to fill in idiomatic Python given the contract
+ test cases + spec context. If a task implementer reports BLOCKED or NEEDS_CONTEXT,
hand them the spec + the per-task description here as additional context.
