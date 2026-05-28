# Auto-mod Auto-Merge + Rollback Ref Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable auto-merge of auto-mod proposals to master, with a one-keystroke rollback path via a pre-merge git ref.

**Architecture:** A new `JARVIS_AUTOMOD_AUTO_MERGE=1` env flag enables a new tail block in `bin/jarvis-automod-impl` that: saves `refs/automod-rollback/<id>` pointing at master pre-merge HEAD, merges the proposal branch with `--no-ff`, pushes master, restarts voice-agent. Recovery: `bin/jarvis-automod revert <id>` looks up the rollback ref from the artifact JSON and hard-resets master with `--force-with-lease`. Strictly additive — the existing manual-merge path is unchanged.

**Tech Stack:** Bash (wrapper), Python 3.13 (finalize + CLI), git plumbing (`update-ref`, `--force-with-lease`).

**Spec:** `docs/superpowers/specs/2026-05-28-automod-auto-merge-rollback-design.md`

---

## File structure

| File | Responsibility | New/Modified |
|---|---|---|
| `src/voice-agent/pipeline/automod/finalize.py` | Add `mark_auto_merged(id, rollback_ref, rollback_sha, merge_sha)` + new `mark-auto-merged` argparse subcommand | Modified |
| `src/voice-agent/pipeline/automod/cli.py` | Extend `revert` to accept an automod ID (looks up rollback ref from artifact); preserve legacy SHA-based revert | Modified |
| `src/voice-agent/pipeline/automod/_state.py` | Add `bin/jarvis-automod-impl` + `bin/jarvis-automod` to `HARD_BLOCKLIST_PATHS` | Modified |
| `bin/jarvis-automod-impl` | Add Stage 7 auto-merge tail block (~70 lines bash) | Modified |
| `src/voice-agent/tests/test_automod_auto_merge.py` | 7 unit tests | Created |

5 tasks below + 1 manual smoke task.

---

## Task 1: Add `mark_auto_merged` helper to `finalize.py`

**Files:**
- Modify: `src/voice-agent/pipeline/automod/finalize.py`
- Test: `src/voice-agent/tests/test_automod_auto_merge.py` (new file)

- [ ] **Step 1: Read the existing finalize.py structure**

```bash
grep -n "^def \|argparse\|sub.add_parser\|^if __name__" /home/ulrich/Documents/Projects/jarvis/src/voice-agent/pipeline/automod/finalize.py | head -20
```

You'll see the existing function definitions + argparse subcommand setup. Match this style for the new helper + subcommand.

- [ ] **Step 2: Write the failing tests**

Create `src/voice-agent/tests/test_automod_auto_merge.py` with EXACTLY this content:

```python
"""Tests for the auto-merge + rollback ref feature.
Spec 2026-05-28."""
from __future__ import annotations

import json
import sqlite3
import sys
import unittest.mock as mock
from pathlib import Path

import pytest


@pytest.fixture
def automod_home(tmp_path, monkeypatch):
    """Isolate ~/.jarvis/auto-mods to tmp."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "jarvis-home"))
    home = tmp_path / "jarvis-home" / "auto-mods"
    home.mkdir(parents=True, exist_ok=True)
    yield home


def test_mark_auto_merged_creates_new_artifact_when_missing(automod_home):
    """If no artifact exists yet, mark_auto_merged creates a minimal one
    so the revert path can find it."""
    from pipeline.automod.finalize import mark_auto_merged
    mark_auto_merged(
        "automod-2026-05-28-aaaa",
        rollback_ref="refs/automod-rollback/automod-2026-05-28-aaaa",
        rollback_sha="abc123",
        merge_sha="def456",
    )
    artifact = automod_home / "automod-2026-05-28-aaaa.json"
    assert artifact.exists()
    rec = json.loads(artifact.read_text(encoding="utf-8"))
    assert rec["id"] == "automod-2026-05-28-aaaa"
    assert rec["rollback_ref"] == "refs/automod-rollback/automod-2026-05-28-aaaa"
    assert rec["rollback_sha"] == "abc123"
    assert rec["merge_sha"] == "def456"
    assert "auto_merged_at" in rec


def test_mark_auto_merged_updates_existing_artifact(automod_home):
    """If an artifact already exists (from the normal finalize flow),
    mark_auto_merged should ADD the rollback metadata, not overwrite the
    record entirely."""
    from pipeline.automod.finalize import mark_auto_merged
    artifact = automod_home / "automod-2026-05-28-bbbb.json"
    existing = {
        "id": "automod-2026-05-28-bbbb",
        "branch": "automod/automod-2026-05-28-bbbb",
        "files_changed": ["src/voice-agent/foo.py"],
        "test_status": "passed",
    }
    artifact.write_text(json.dumps(existing), encoding="utf-8")

    mark_auto_merged(
        "automod-2026-05-28-bbbb",
        rollback_ref="refs/automod-rollback/automod-2026-05-28-bbbb",
        rollback_sha="cafe1",
        merge_sha="cafe2",
    )
    rec = json.loads(artifact.read_text(encoding="utf-8"))
    # Original fields preserved
    assert rec["branch"] == "automod/automod-2026-05-28-bbbb"
    assert rec["files_changed"] == ["src/voice-agent/foo.py"]
    assert rec["test_status"] == "passed"
    # New fields added
    assert rec["rollback_ref"] == "refs/automod-rollback/automod-2026-05-28-bbbb"
    assert rec["rollback_sha"] == "cafe1"
    assert rec["merge_sha"] == "cafe2"


def test_mark_auto_merged_is_idempotent(automod_home):
    """Re-calling overwrites the rollback metadata + auto_merged_at.
    Should not crash."""
    from pipeline.automod.finalize import mark_auto_merged
    mark_auto_merged("automod-2026-05-28-cccc", rollback_ref="r1",
                     rollback_sha="s1", merge_sha="m1")
    mark_auto_merged("automod-2026-05-28-cccc", rollback_ref="r2",
                     rollback_sha="s2", merge_sha="m2")
    rec = json.loads(
        (automod_home / "automod-2026-05-28-cccc.json").read_text()
    )
    assert rec["rollback_ref"] == "r2"
    assert rec["rollback_sha"] == "s2"
    assert rec["merge_sha"] == "m2"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_auto_merge.py -v`

Expected: FAIL with `ImportError: cannot import name 'mark_auto_merged' from 'pipeline.automod.finalize'`.

- [ ] **Step 4: Read finalize.py to understand imports + structure**

```bash
head -50 /home/ulrich/Documents/Projects/jarvis/src/voice-agent/pipeline/automod/finalize.py
```

Note the existing imports (especially `from pipeline.automod._state import artifact_path`, `audit`, etc.) and the existing `_now_iso()` helper if present (or add it).

- [ ] **Step 5: Add `mark_auto_merged` function to finalize.py**

Append to `src/voice-agent/pipeline/automod/finalize.py` (after the existing functions, before any `if __name__ == "__main__":` block):

```python
def mark_auto_merged(
    automod_id: str,
    rollback_ref: str,
    rollback_sha: str,
    merge_sha: str,
) -> None:
    """Stamp an automod artifact JSON with auto-merge metadata.

    Idempotent — overwrites the auto_merged_at + rollback fields on
    repeat calls. If no artifact exists yet (wrapper crashed before
    normal finalize ran), creates a minimal record so the revert path
    can find it. Spec 2026-05-28."""
    artifact_file = artifact_path(automod_id)
    if artifact_file.exists():
        try:
            record = json.loads(artifact_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            record = {"id": automod_id}
    else:
        artifact_file.parent.mkdir(parents=True, exist_ok=True)
        record = {"id": automod_id}

    record["auto_merged_at"] = _now_iso()
    record["rollback_ref"] = rollback_ref
    record["rollback_sha"] = rollback_sha
    record["merge_sha"] = merge_sha
    artifact_file.write_text(json.dumps(record, indent=2),
                             encoding="utf-8")
    try:
        audit("automod_auto_merged",
              id=automod_id,
              rollback_ref=rollback_ref,
              rollback_sha=rollback_sha,
              merge_sha=merge_sha)
    except Exception:
        pass  # audit failures must never break the flow
```

If `_now_iso` doesn't exist in finalize.py, look for it in `pipeline/automod/error_logger.py` or `patterns.py` and either import it OR add a local copy:

```python
def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
```

If `audit` isn't imported, import it from `pipeline.automod.artifact` (or wherever it lives — check via `grep -n "def audit\|from .* import audit" src/voice-agent/pipeline/automod/*.py`).

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_auto_merge.py -v`

Expected: ALL 3 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/voice-agent/pipeline/automod/finalize.py src/voice-agent/tests/test_automod_auto_merge.py
git commit -m "automod: add mark_auto_merged helper for rollback metadata"
```

---

## Task 2: Add `mark-auto-merged` argparse subcommand to finalize.py CLI

**Files:**
- Modify: `src/voice-agent/pipeline/automod/finalize.py`

- [ ] **Step 1: Locate the existing argparse setup**

```bash
grep -n "argparse\|add_parser\|set_defaults\|sub.add_parser" /home/ulrich/Documents/Projects/jarvis/src/voice-agent/pipeline/automod/finalize.py
```

You'll find the existing subparser registration. The new `mark-auto-merged` subcommand goes alongside the existing ones.

- [ ] **Step 2: Add the subparser**

In `src/voice-agent/pipeline/automod/finalize.py`, find the argparse block (probably near `if __name__ == "__main__":` or inside a `_cli()` helper). Add a new subparser:

```python
    # Auto-merge stamping subcommand (Spec 2026-05-28).
    mp = sub.add_parser(
        "mark-auto-merged",
        help="Stamp artifact with auto-merge metadata (called by wrapper)",
    )
    mp.add_argument("id")
    mp.add_argument("--rollback-ref", required=True)
    mp.add_argument("--rollback-sha", required=True)
    mp.add_argument("--merge-sha", required=True)
    mp.set_defaults(handler=lambda args:
                    mark_auto_merged(args.id, args.rollback_ref,
                                     args.rollback_sha, args.merge_sha))
```

If the existing argparse uses a different style (e.g. dispatching on `args.cmd` instead of `set_defaults(handler=...)`), match THAT style — not the one above.

- [ ] **Step 3: Test the CLI directly**

Run:

```bash
cd src/voice-agent && JARVIS_HOME=/tmp/jarvis-cli-test .venv/bin/python -m pipeline.automod.finalize mark-auto-merged \
    automod-2026-05-28-zzzz \
    --rollback-ref refs/automod-rollback/automod-2026-05-28-zzzz \
    --rollback-sha abc123 \
    --merge-sha def456
```

Expected: command exits 0. Then verify:

```bash
cat /tmp/jarvis-cli-test/auto-mods/automod-2026-05-28-zzzz.json
```

Should show the JSON record with `auto_merged_at`, `rollback_ref`, etc.

- [ ] **Step 4: Run the unit tests again to confirm no regression**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_auto_merge.py -v`

Expected: 3 still pass.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/pipeline/automod/finalize.py
git commit -m "automod: add mark-auto-merged CLI subcommand"
```

---

## Task 3: Extend HARD_BLOCKLIST to protect rollback machinery

**Files:**
- Modify: `src/voice-agent/pipeline/automod/_state.py`
- Test: `src/voice-agent/tests/test_automod_auto_merge.py` (add 1 test)

- [ ] **Step 1: Write the failing test**

Append to `src/voice-agent/tests/test_automod_auto_merge.py`:

```python
def test_blocklist_includes_automod_wrappers():
    """The CLI + wrapper scripts must be on the blocklist so auto-mod
    can't propose fixes to its own rollback machinery."""
    from pipeline.automod._state import HARD_BLOCKLIST_PATHS
    assert "bin/jarvis-automod-impl" in HARD_BLOCKLIST_PATHS
    assert "bin/jarvis-automod" in HARD_BLOCKLIST_PATHS


def test_is_blocked_path_rejects_wrapper_edits():
    """The is_blocked_path helper should return True for the wrappers."""
    from pipeline.automod._state import is_blocked_path
    assert is_blocked_path("bin/jarvis-automod-impl") is True
    assert is_blocked_path("bin/jarvis-automod") is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_auto_merge.py -v -k "blocklist or blocked_path"`

Expected: FAIL — neither entry is in the blocklist yet.

- [ ] **Step 3: Add the entries to HARD_BLOCKLIST_PATHS**

In `src/voice-agent/pipeline/automod/_state.py`, find `HARD_BLOCKLIST_PATHS = (` and add 2 entries:

```python
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
    # 2026-05-28: protect the auto-merge wrapper + CLI from being
    # modified by auto-mod itself. The rollback ref path uses these
    # scripts; if auto-mod could rewrite them, a bad fix could break
    # its own rollback.
    "bin/jarvis-automod-impl",
    "bin/jarvis-automod",
)
```

Wait — note that the existing `ALLOWED_PATH_PREFIX = "src/voice-agent/"` means files outside `src/voice-agent/` are already blocked by `is_blocked_path`. So adding `bin/jarvis-automod-impl` to HARD_BLOCKLIST_PATHS is REDUNDANT for the prefix-block reason, but it's still useful as documentation + explicit signal.

Verify the existing `is_blocked_path` returns True for these paths BEFORE the change too:

```bash
cd src/voice-agent && .venv/bin/python -c "from pipeline.automod._state import is_blocked_path; print(is_blocked_path('bin/jarvis-automod-impl'))"
```

If it already prints `True` (because of the prefix block), the test will pass without the HARD_BLOCKLIST_PATHS addition — but adding them is still spec-correct (explicit > implicit).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_auto_merge.py -v -k "blocklist or blocked_path"`

Expected: BOTH new tests pass.

- [ ] **Step 5: Run the full new test file to confirm no regression**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_auto_merge.py -v`

Expected: 5 tests pass (3 from Task 1 + 2 new).

Also verify no existing automod test broke:

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/ -v -k "automod" 2>&1 | tail -10`

Expected: All automod tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/voice-agent/pipeline/automod/_state.py src/voice-agent/tests/test_automod_auto_merge.py
git commit -m "automod: extend HARD_BLOCKLIST with bin/jarvis-automod-impl + bin/jarvis-automod"
```

---

## Task 4: Extend `revert` CLI to accept an automod ID

**Files:**
- Modify: `src/voice-agent/pipeline/automod/cli.py`
- Test: `src/voice-agent/tests/test_automod_auto_merge.py` (add 2 tests)

- [ ] **Step 1: Read the existing revert function**

```bash
grep -n "def revert\|revert" /home/ulrich/Documents/Projects/jarvis/src/voice-agent/pipeline/automod/cli.py | head -10
```

Read 20-30 lines around the `def revert` definition to understand the current shape.

- [ ] **Step 2: Write the failing tests**

Append to `src/voice-agent/tests/test_automod_auto_merge.py`:

```python
def test_revert_by_automod_id_reads_rollback_ref(automod_home):
    """Revert with an automod ID should look up the rollback ref from
    the artifact and use it for git reset."""
    from pipeline.automod import cli as automod_cli
    artifact = automod_home / "automod-2026-05-28-dddd.json"
    artifact.write_text(json.dumps({
        "id": "automod-2026-05-28-dddd",
        "rollback_ref": "refs/automod-rollback/automod-2026-05-28-dddd",
        "rollback_sha": "deadbeef",
        "merge_sha": "feedface",
    }), encoding="utf-8")

    with mock.patch("subprocess.check_call") as mock_check, \
         mock.patch("subprocess.run") as mock_run:
        # Simulate argparse Namespace
        class _Args:
            target = "automod-2026-05-28-dddd"
        rc = automod_cli.revert(_Args())
    assert rc == 0
    # check_call should have been invoked at least with a git reset --hard.
    calls = [args[0] for args, _kw in mock_check.call_args_list]
    flat = [item for sublist in calls for item in sublist]
    assert "reset" in flat
    assert "--hard" in flat
    assert "deadbeef" in flat


def test_revert_by_automod_id_returns_2_when_artifact_missing(automod_home):
    from pipeline.automod import cli as automod_cli
    class _Args:
        target = "automod-2026-05-28-eeee"  # doesn't exist
    rc = automod_cli.revert(_Args())
    assert rc == 2


def test_revert_by_automod_id_returns_2_when_no_rollback_metadata(automod_home):
    """Artifact exists but has no rollback_ref/sha (e.g. it was manually
    merged via the legacy path) → reject."""
    from pipeline.automod import cli as automod_cli
    artifact = automod_home / "automod-2026-05-28-ffff.json"
    artifact.write_text(json.dumps({
        "id": "automod-2026-05-28-ffff",
        "branch": "automod/automod-2026-05-28-ffff",
        # No rollback_ref, no rollback_sha
    }), encoding="utf-8")

    class _Args:
        target = "automod-2026-05-28-ffff"
    rc = automod_cli.revert(_Args())
    assert rc == 2
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_auto_merge.py -v -k "revert"`

Expected: FAIL — revert doesn't handle automod IDs yet (or behaves wrong).

- [ ] **Step 4: Modify the `revert` function in cli.py**

In `src/voice-agent/pipeline/automod/cli.py`, find the existing `def revert(args)` function. Replace its body to branch on whether `target` looks like an automod ID:

```python
def revert(args) -> int:
    """Revert an auto-merged automod proposal OR a SHA.

    If `args.target` matches the pattern `automod-YYYY-MM-DD-xxxxxx`,
    look up the rollback ref from the artifact JSON and hard-reset
    master to it (with --force-with-lease push). Restarts the
    voice-agent so the reverted code is live.

    Otherwise falls through to the legacy SHA-based revert."""
    import json as _json
    import subprocess
    import sys
    from pipeline.automod._state import artifact_path, _automod_home

    target = args.target

    # New path: automod ID lookup → rollback ref reset.
    if target.startswith("automod-") and "/" not in target:
        artifact_file = artifact_path(target)
        if not artifact_file.exists():
            print(f"error: artifact not found: {artifact_file}",
                  file=sys.stderr)
            return 2
        try:
            rec = _json.loads(artifact_file.read_text(encoding="utf-8"))
        except (OSError, _json.JSONDecodeError) as e:
            print(f"error: cannot read artifact {target}: {e}",
                  file=sys.stderr)
            return 2
        rollback_ref = rec.get("rollback_ref")
        rollback_sha = rec.get("rollback_sha")
        if not rollback_ref or not rollback_sha:
            print(f"error: artifact {target} has no rollback metadata "
                  "(was it manually merged?)",
                  file=sys.stderr)
            return 2
        # Fetch the rollback ref from origin (in case it's not local).
        try:
            subprocess.check_call(
                ["git", "fetch", "origin",
                 f"{rollback_ref}:{rollback_ref}"],
            )
        except subprocess.CalledProcessError:
            # The ref might not exist on origin if the push failed.
            # Continue with the local SHA — git reset --hard takes a
            # commit-ish, not necessarily a ref.
            pass
        subprocess.check_call(["git", "checkout", "master"])
        subprocess.check_call(["git", "reset", "--hard", rollback_sha])
        subprocess.check_call(
            ["git", "push", "--force-with-lease",
             "origin", "master:master"],
        )
        # Restart the voice-agent so the reverted code is live.
        subprocess.run(
            ["systemctl", "--user", "restart",
             "jarvis-voice-agent.service"], check=False,
        )
        try:
            from pipeline.automod.artifact import audit
            audit("automod_reverted",
                  id=target, rollback_ref=rollback_ref,
                  rollback_sha=rollback_sha)
        except Exception:
            pass
        print(f"reverted: master reset to {rollback_sha[:8]} "
              f"(rollback ref {rollback_ref})")
        return 0

    # Legacy path: SHA-based revert (creates an inverse commit).
    return _legacy_revert_by_sha(args)
```

Rename the existing revert body to `_legacy_revert_by_sha(args)` (keep its behavior identical):

```python
def _legacy_revert_by_sha(args) -> int:
    """Existing SHA-based revert path. Creates an inverse commit via
    `git revert <sha>`. Preserved for backward compatibility."""
    # [paste the original revert body here]
```

If the existing `revert` function imports modules at the top of the file, move those imports up if they're not already there.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_auto_merge.py -v`

Expected: ALL 8 tests pass (3 mark_auto_merged + 2 blocklist + 3 revert).

- [ ] **Step 6: Verify the CLI still works for legacy SHA-based revert**

```bash
cd /home/ulrich/Documents/Projects/jarvis && bin/jarvis-automod 2>&1 | head -5
```

Expected: usage line printed, no errors.

- [ ] **Step 7: Commit**

```bash
git add src/voice-agent/pipeline/automod/cli.py src/voice-agent/tests/test_automod_auto_merge.py
git commit -m "automod: revert CLI accepts automod ID with rollback-ref lookup"
```

---

## Task 5: Add Stage 7 auto-merge tail to `bin/jarvis-automod-impl`

**Files:**
- Modify: `bin/jarvis-automod-impl`

- [ ] **Step 1: Read the existing wrapper**

```bash
cat /home/ulrich/Documents/Projects/jarvis/bin/jarvis-automod-impl
```

Identify the END of the existing flow (where the regular `finalize.py` call lands). Insert the new Stage 7 block AFTER that.

You'll need to know:
- The variable holding the automod ID (likely `$ID`)
- The variable holding the proposal branch (likely `$BRANCH`)
- The variable indicating pytest passed (may or may not exist; you may need to add a tracker)
- The variable indicating finalize passed (similarly)

If those vars don't exist explicitly, ADD them: capture the exit codes of pytest + finalize calls into shell variables.

- [ ] **Step 2: Add tracker variables (if not present)**

Find where pytest is run in the wrapper. Wrap its invocation:

```bash
PYTEST_PASSED=0
if python -m pytest src/voice-agent/tests/ -q --timeout=60; then
    PYTEST_PASSED=1
fi
```

Similarly for finalize:

```bash
FINALIZE_PASSED=0
if "$REPO_ROOT/src/voice-agent/.venv/bin/python" -m pipeline.automod.finalize check "$ID"; then
    FINALIZE_PASSED=1
fi
```

(The exact `finalize` invocation depends on the existing flow — adapt if it's different.)

- [ ] **Step 3: Append Stage 7 auto-merge block**

At the END of the wrapper (just before the `} 2>&1 | tee -a "$LOG"` closing block if there is one), insert:

```bash
# ===== Stage 7: Auto-merge (Spec 2026-05-28) =====
# Fires only when JARVIS_AUTOMOD_AUTO_MERGE=1 AND all gates green.
# Saves refs/automod-rollback/<id> pointing at master pre-merge HEAD,
# merges proposal branch, pushes master, restarts voice-agent.
if [ "${JARVIS_AUTOMOD_AUTO_MERGE:-0}" = "1" ] \
   && [ "${FINALIZE_PASSED:-0}" = "1" ] \
   && [ "${PYTEST_PASSED:-0}" = "1" ]; then
    echo "[$(date -u +%FT%TZ)] [automod-impl] AUTO_MERGE=1 — proceeding"

    # 7a. Save rollback ref BEFORE touching master.
    if ! git fetch origin master --quiet; then
        echo "[$(date -u +%FT%TZ)] [automod-impl] origin fetch FAILED — aborting auto-merge"
        exit 0
    fi
    PRE_MERGE_SHA=$(git rev-parse origin/master)
    ROLLBACK_REF="refs/automod-rollback/$ID"
    git update-ref "$ROLLBACK_REF" "$PRE_MERGE_SHA"
    if ! git push origin "$ROLLBACK_REF:$ROLLBACK_REF" --quiet; then
        echo "[$(date -u +%FT%TZ)] [automod-impl] rollback ref push FAILED — aborting auto-merge"
        exit 0
    fi

    # 7b. Merge proposal branch into master.
    if ! git checkout master --quiet; then
        echo "[$(date -u +%FT%TZ)] [automod-impl] checkout master FAILED — aborting"
        exit 0
    fi
    git pull origin master --ff-only --quiet || true
    if ! git merge --no-ff "$BRANCH" -m "automod: auto-merge $ID" --quiet; then
        echo "[$(date -u +%FT%TZ)] [automod-impl] merge FAILED — aborting + reset"
        git merge --abort 2>/dev/null || true
        exit 0
    fi

    # 7c. Push master.
    if ! git push origin master --quiet; then
        echo "[$(date -u +%FT%TZ)] [automod-impl] master push FAILED — reverting local merge"
        git reset --hard "$PRE_MERGE_SHA"
        exit 0
    fi

    MERGE_SHA=$(git rev-parse master)
    echo "[$(date -u +%FT%TZ)] [automod-impl] auto-merged: id=$ID merge_sha=$MERGE_SHA"

    # 7d. Stamp the artifact JSON with auto-merge metadata.
    "$REPO_ROOT/src/voice-agent/.venv/bin/python" \
        -m pipeline.automod.finalize mark-auto-merged \
        "$ID" \
        --rollback-ref "$ROLLBACK_REF" \
        --rollback-sha "$PRE_MERGE_SHA" \
        --merge-sha "$MERGE_SHA"

    # 7e. Restart the voice-agent so the new code is live.
    # Respect the existing "active session" guard from CLAUDE.md.
    LAST_TURN_TS=$(sqlite3 ~/.local/share/jarvis/turn_telemetry.db \
        "SELECT strftime('%s', ts_utc) FROM turns ORDER BY id DESC LIMIT 1" 2>/dev/null || echo "0")
    NOW=$(date +%s)
    AGE=$((NOW - LAST_TURN_TS))
    if [ "$AGE" -gt 60 ]; then
        systemctl --user restart jarvis-voice-agent.service
        echo "[$(date -u +%FT%TZ)] [automod-impl] voice-agent restarted"
    else
        echo "[$(date -u +%FT%TZ)] [automod-impl] restart deferred (session active ${AGE}s ago); new code loads on next natural restart"
    fi
fi
```

- [ ] **Step 4: Verify the bash syntax**

```bash
bash -n /home/ulrich/Documents/Projects/jarvis/bin/jarvis-automod-impl && echo "syntax OK"
```

Expected: `syntax OK`. If you have `shellcheck` installed: `shellcheck bin/jarvis-automod-impl` should pass (warnings are OK; errors are not).

- [ ] **Step 5: Smoke test the inner logic (offline, no actual auto-merge)**

Since the auto-merge is inside `if [ "${JARVIS_AUTOMOD_AUTO_MERGE:-0}" = "1" ]`, with the env var UNSET the new block is a complete no-op. Verify the wrapper still parses + runs cleanly with auto-merge off:

```bash
JARVIS_AUTOMOD_AUTO_MERGE=0 bash -n /home/ulrich/Documents/Projects/jarvis/bin/jarvis-automod-impl && echo "no-op path OK"
```

(Full integration testing happens in Task 6 — manual smoke against live agent.)

- [ ] **Step 6: Commit**

```bash
git add bin/jarvis-automod-impl
git commit -m "automod: add Stage 7 auto-merge tail to wrapper script"
```

---

## Task 6: Manual smoke test against live voice-agent

This task requires the live voice-agent + a real auto-mod intent firing through the full pipeline. Mandatory before claiming auto-merge is live.

- [ ] **Step 1: Confirm both env flags are set**

```bash
systemctl --user show jarvis-voice-agent.service -p Environment | tr ' ' '\n' | grep -i automod
```

Expected: BOTH `JARVIS_AUTOMOD_ENABLED=1` AND `JARVIS_AUTOMOD_SPAWN_LIVE=1`. If `JARVIS_AUTOMOD_AUTO_MERGE` is NOT yet set, add it now via:

Edit `~/.config/systemd/user/jarvis-voice-agent.service` to add `Environment=JARVIS_AUTOMOD_AUTO_MERGE=1` immediately after the `SPAWN_LIVE=1` line.

```bash
systemctl --user daemon-reload && systemctl --user restart jarvis-voice-agent.service
```

Verify:
```bash
systemctl --user show jarvis-voice-agent.service -p Environment | tr ' ' '\n' | grep -i automod
```

Expected: all three flags `=1`.

- [ ] **Step 2: Force an intent into the queue**

Use the `propose_code_mod` voice tool. Say to JARVIS: *"Jarvis, propose a code mod: change the front-loaded ack threshold from 1500 to 1200 milliseconds in jarvis_agent.py."*

Or write directly to the queue:

```bash
ID="automod-2026-05-28-smoketest-$$"
cat >> ~/.jarvis/auto-mods/queue.jsonl << EOF
{"id": "$ID", "kind": "explicit", "intent": "In src/voice-agent/jarvis_agent.py, find the line that says 'await asyncio.sleep(1.5)' inside the _front_loaded_ack closure and change it to 'await asyncio.sleep(1.4)'. Make NO OTHER CHANGES.", "rationale": "auto-merge smoke test", "created_at": "$(date -u +%FT%TZ)"}
EOF
```

(This is a TRIVIAL, REVERSIBLE change. Pick something this small for the smoke test.)

- [ ] **Step 3: Watch the wrapper run**

```bash
tail -F ~/.jarvis/auto-mods/${ID}.log
```

Expected progression:
1. `starting id=$ID`
2. `branch=automod/$ID`
3. Subagent runs (claude-code CLI output)
4. `pytest passed`
5. `finalize passed`
6. `AUTO_MERGE=1 — proceeding`
7. `auto-merged: id=$ID merge_sha=...`
8. `voice-agent restarted`

- [ ] **Step 4: Verify master moved**

```bash
git fetch origin master && git log origin/master --oneline -3
```

Expected: top commit is `automod: auto-merge automod-2026-05-28-smoketest-...`

- [ ] **Step 5: Verify rollback ref exists on origin**

```bash
git ls-remote origin "refs/automod-rollback/*" | head -5
```

Expected: at least one ref `refs/automod-rollback/$ID` listed.

- [ ] **Step 6: Verify artifact has auto-merge metadata**

```bash
cat ~/.jarvis/auto-mods/${ID}.json | python3 -m json.tool
```

Expected: JSON with `auto_merged_at`, `rollback_ref`, `rollback_sha`, `merge_sha` fields populated.

- [ ] **Step 7: Verify voice-agent restarted with new code**

```bash
grep -E "registered worker|error telemetry handler installed" ~/.local/share/jarvis/logs/voice-agent.log | tail -5
```

Expected: a "registered worker" line with timestamp AFTER the auto-merge timestamp.

- [ ] **Step 8: Smoke the revert**

```bash
bin/jarvis-automod revert $ID
```

Expected:
- `reverted: master reset to <sha>` printed
- `git log master --oneline -1` shows the original master HEAD (before the auto-merge)
- Voice-agent restarted again (back to pre-merge code)

- [ ] **Step 9: Confirm the system is back to known-good**

```bash
git fetch origin && git log origin/master --oneline -3
```

Expected: master is at the pre-auto-merge SHA.

```bash
tail -100 ~/.local/share/jarvis/logs/voice-agent.log | grep "registered worker" | tail -1
```

Expected: a fresh "registered worker" line — the agent restarted post-revert and is running the original code.

- [ ] **Step 10: Cleanup the smoke-test rollback ref (optional)**

```bash
git push origin :refs/automod-rollback/$ID
```

(Deletes the smoke-test rollback ref from origin. Local ref can be cleaned via `git update-ref -d refs/automod-rollback/$ID`.)

If all steps pass, the auto-merge + rollback feature is live and verified end-to-end.

---

## Verification checklist (Spec coverage)

| Spec part | Implementation task |
|---|---|
| Part 1 (env gate `JARVIS_AUTOMOD_AUTO_MERGE`) | Task 5 (referenced in Stage 7 conditional) + Task 6 (sets it in unit file) |
| Part 2 (auto-merge sequence in wrapper) | Task 5 |
| Part 3 (`mark_auto_merged` helper + CLI subcommand) | Tasks 1 + 2 |
| Part 4 (extend `revert` CLI) | Task 4 |
| Part 5 (risk + safety: HARD_BLOCKLIST extension) | Task 3 |
| Part 6 (testing — 7 unit tests + 1 manual) | Tasks 1, 3, 4 (8 unit tests total — exceeded spec); Task 6 (manual) |
| Part 7 (verification path) | Task 6 (10 steps) |
| Part 8 (deliberate out-of-scope) | Not implemented (correct) |
