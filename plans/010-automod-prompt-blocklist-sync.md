# Plan 010: Derive the build-prompt blocklist from `HARD_BLOCKLIST_PATHS` so layer-1 can't drift

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan in
> `plans/README.md`.
>
> **Drift check (run first)**:
> `git diff --stat e04d31c8..HEAD -- bin/jarvis-automod-impl src/voice-agent/pipeline/automod/_state.py`
> If either file changed since this plan was written, compare the "Current state"
> excerpts against the live code before proceeding; on a mismatch, STOP.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none (independent of 009, but naturally lands with it)
- **Category**: security / tech-debt
- **Planned at**: commit `e04d31c8`, 2026-06-27

## Why this matters

The auto-mod blocklist is enforced in "three independent layers"
(`.claude/rules/regression-prevention.md` §8). Layer 1 is the HARD-RULES list in
the build prompt inside `bin/jarvis-automod-impl`. That list is **hand-copied**
and has already drifted from the source of truth `HARD_BLOCKLIST_PATHS` in
`_state.py`: the prompt is **missing `src/voice-agent/desktop-tauri/` and
`src/voice-agent/evolution/`** (both added to `HARD_BLOCKLIST_PATHS` on
2026-06-24). Because both live under `src/voice-agent/`, the prompt's "edit only
under `src/voice-agent/`" rule *permits* them while the explicit don't-touch list
omits them — so the build agent is told it may edit the human-owned fitness gate
(`evolution/`) and the desktop shell. Layers 2/3 still reject such a diff, so the
*result* is a wasted ~25-minute build that finalize rejects as `blocked_path`,
not an escape — but layer 1 is no longer faithful, which is precisely the
defense-in-depth property §8 says each layer must hold. After this plan the
prompt's blocklist is generated from `HARD_BLOCKLIST_PATHS` at build time, so the
two can never diverge again, and a regression test guards the fallback.

## Current state

- `bin/jarvis-automod-impl` is the bash wrapper the spawner runs per intent. It
  builds a `PROMPT` heredoc (lines ~66-99) with a hand-written blocklist:
  ```bash
  PROMPT="You are JARVIS auto-mod. A targeted code change has been
  requested:

  ${INTENT_BODY}

  ${RESEARCH_SECTION}HARD RULES (violating ANY means the spawner will reject your commit and delete this branch):

  1. Edit ONLY files under src/voice-agent/.
  2. Do NOT touch ANY of these paths:
     - src/voice-agent/sanitizers/
     - src/voice-agent/confab_detector.py
     - src/voice-agent/pipeline/automod/
     - src/voice-agent/pipeline/skill_review.py
     - src/voice-agent/prompts/soul.md
     - CLAUDE.md
     - .claude/rules/regression-prevention.md
     - MEMORY.md
     - USER.md
  3. Do NOT delete tests or weaken assertions. You MAY add tests.
  ...
  ```
  The script already has `TOOLING_ROOT` (line ~24) and uses
  `"$TOOLING_ROOT/src/voice-agent/.venv/bin/python"` elsewhere (e.g. the finalize
  call at line ~126). `set -euo pipefail` is active (line 9).
- `src/voice-agent/pipeline/automod/_state.py:105-133` defines the truth:
  ```python
  HARD_BLOCKLIST_PATHS = (
      "src/voice-agent/desktop-tauri/",
      "src/voice-agent/sanitizers/",
      "src/voice-agent/confab_detector.py",
      "src/voice-agent/pipeline/automod/",
      "src/voice-agent/evolution/",
      "src/voice-agent/pipeline/skill_review.py",
      "src/voice-agent/prompts/soul.md",
      "CLAUDE.md",
      ".claude/rules/regression-prevention.md",
      "MEMORY.md",
      "USER.md",
      "bin/jarvis-automod-impl",
      "bin/jarvis-automod",
      "bin/jarvis-evolution-watchdog",
      "bin/jarvis-evolution-nightly",
      "bin/jarvis-evolution-ondemand",
  )
  ```
  The module is import-safe, stdlib-only, no import-time side effects — so it can
  be imported by a one-off `python -c` without booting the agent.

## Commands you will need

| Purpose | Command | Expected |
|---------|---------|----------|
| Derive list (smoke) | `PYTHONPATH=src/voice-agent src/voice-agent/.venv/bin/python -c 'from pipeline.automod._state import HARD_BLOCKLIST_PATHS as B; print("\n".join("   - "+p for p in B))'` | 16 lines incl. `desktop-tauri/` and `evolution/` |
| Wrapper syntax check | `bash -n bin/jarvis-automod-impl` | exit 0 |
| Sync regression test | `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_impl_blocklist_synced.py -q` | passes (created in Step 3) |

## Scope

**In scope**:
- `bin/jarvis-automod-impl`
- `src/voice-agent/tests/test_automod_impl_blocklist_synced.py` (create)

**Out of scope**:
- `_state.py` — read-only here; do not change `HARD_BLOCKLIST_PATHS`.
- The rest of the wrapper's logic (worktree branch, research stage, finalize call,
  the AUTO_MERGE block) — leave untouched.
- `bin/jarvis` and the CLI — off-limits.

## Git workflow

- Branch off `master`: `git checkout -b advisor/010-prompt-blocklist-sync`.
- **NEVER `git add -A`** (repo carries many unrelated uncommitted files from
  parallel sessions). Stage explicitly:
  `git add bin/jarvis-automod-impl src/voice-agent/tests/test_automod_impl_blocklist_synced.py`
  then `git commit -- <those paths>`; verify with `git show --stat HEAD`.
- Conventional commit, e.g. `fix(automod): derive build-prompt blocklist from HARD_BLOCKLIST_PATHS`.
- **No `Co-Authored-By` trailer / no Claude Code attribution.**

## Steps

### Step 1: Generate the blocklist lines before the PROMPT heredoc

Just above where `PROMPT=` is assigned, add a derivation that asks the tooling
interpreter for the canonical list, with a hardcoded fallback that fails **safe**
(never an empty blocklist). Target shape:
```bash
# Layer-1 blocklist is DERIVED from pipeline.automod._state.HARD_BLOCKLIST_PATHS
# (the same tuple finalize/merge enforce) so the prompt can never drift from the
# enforced truth again. Fallback keeps the prompt blocklist-safe if the import
# ever fails — it must list every current HARD_BLOCKLIST entry.
BLOCKLIST_LINES="$(PYTHONPATH="$TOOLING_ROOT/src/voice-agent" \
  "$TOOLING_ROOT/src/voice-agent/.venv/bin/python" -c \
  'from pipeline.automod._state import HARD_BLOCKLIST_PATHS as B; print("\n".join("   - "+p for p in B))' \
  2>/dev/null)"
if [ -z "$BLOCKLIST_LINES" ]; then
  BLOCKLIST_LINES="   - src/voice-agent/desktop-tauri/
   - src/voice-agent/sanitizers/
   - src/voice-agent/confab_detector.py
   - src/voice-agent/pipeline/automod/
   - src/voice-agent/evolution/
   - src/voice-agent/pipeline/skill_review.py
   - src/voice-agent/prompts/soul.md
   - CLAUDE.md
   - .claude/rules/regression-prevention.md
   - MEMORY.md
   - USER.md
   - bin/jarvis-automod-impl
   - bin/jarvis-automod
   - bin/jarvis-evolution-watchdog
   - bin/jarvis-evolution-nightly
   - bin/jarvis-evolution-ondemand"
fi
```
Note: `set -euo pipefail` is active and the `python -c` may legitimately produce
empty output if the venv is missing; the `2>/dev/null` + `$( )` assignment does
not fail the script on a non-zero exit (assignment masks it), and the `-z` guard
handles the empty case. Do **not** wrap it in a way that makes a failed import
abort the build.

### Step 2: Reference the derived list inside the PROMPT

In the `PROMPT=` heredoc, replace the hardcoded item-2 list (the 9 `- ...` lines
under `2. Do NOT touch ANY of these paths:`) with the variable:
```
2. Do NOT touch ANY of these paths:
${BLOCKLIST_LINES}
```
Keep rules 1 and 3-9 exactly as they are.

**Verify**:
```
bash -n bin/jarvis-automod-impl   # syntax OK, exit 0
# Render the prompt in isolation to confirm the new paths appear:
TOOLING_ROOT="$(pwd)" bash -c '
  BLOCKLIST_LINES="$(PYTHONPATH="$TOOLING_ROOT/src/voice-agent" "$TOOLING_ROOT/src/voice-agent/.venv/bin/python" -c "from pipeline.automod._state import HARD_BLOCKLIST_PATHS as B; print(chr(10).join(\"   - \"+p for p in B))" 2>/dev/null)"
  echo "$BLOCKLIST_LINES"' | grep -E 'desktop-tauri/|evolution/'
```
→ the grep prints the `desktop-tauri/` and `evolution/` lines (proving they're now
in the derived block).

### Step 3: Add a regression test that the fallback can't drift

Create `src/voice-agent/tests/test_automod_impl_blocklist_synced.py`. It reads the
wrapper file and asserts the hardcoded **fallback** block contains every entry in
`HARD_BLOCKLIST_PATHS` (so if someone adds a blocklist entry but forgets the
fallback, the test fails). Shape:
```python
"""Guard: bin/jarvis-automod-impl's fallback blocklist must list every
HARD_BLOCKLIST_PATHS entry, so layer-1 can't silently drift from layers 2/3."""
from __future__ import annotations

import sys
from pathlib import Path

_VA_ROOT = Path(__file__).resolve().parent.parent
if str(_VA_ROOT) not in sys.path:
    sys.path.insert(0, str(_VA_ROOT))

_REPO_ROOT = _VA_ROOT.parent.parent  # tests/ -> voice-agent -> src -> repo root


def test_wrapper_fallback_lists_every_blocklist_path():
    from pipeline.automod._state import HARD_BLOCKLIST_PATHS

    wrapper = (_REPO_ROOT / "bin" / "jarvis-automod-impl").read_text(encoding="utf-8")
    missing = [p for p in HARD_BLOCKLIST_PATHS if f"- {p}" not in wrapper]
    assert not missing, f"wrapper fallback blocklist missing: {missing}"
```
(`_REPO_ROOT` resolves the repo root from the test's location; the wrapper lives
at `<repo>/bin/jarvis-automod-impl`.)

**Verify**: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_impl_blocklist_synced.py -q` → passes.

## Test plan

- One new test file, `test_automod_impl_blocklist_synced.py`, asserting the
  fallback ⊇ `HARD_BLOCKLIST_PATHS`. Model after other simple file-reading guard
  tests in `tests/` (plain `def test_*`, no fixtures).
- This test fails *today* if run against the unmodified wrapper (it's missing
  `desktop-tauri/` + `evolution/`) — confirming it actually guards the property.

## Done criteria

ALL must hold:

- [ ] `bash -n bin/jarvis-automod-impl` exits 0
- [ ] The Step 2 render command's grep prints the `desktop-tauri/` and `evolution/` lines
- [ ] `cd src/voice-agent && .venv/bin/python -m pytest tests/test_automod_impl_blocklist_synced.py -q` passes
- [ ] `git show --stat HEAD` lists ONLY the wrapper + the new test file
- [ ] `plans/README.md` status row for 010 updated

## STOP conditions

- The wrapper's PROMPT structure doesn't match the "Current state" excerpt (drift).
- `set -euo pipefail` interacts with the derivation such that a missing venv aborts
  the whole build (it must degrade to the fallback, not exit). If you can't make it
  degrade safely, STOP and report.
- The render verification shows the derived list is empty even with the venv present.

## Maintenance notes

- **`bin/jarvis-automod-impl` is on the auto-mod `HARD_BLOCKLIST`.** Human/normal
  executor only — the auto-mod loop cannot edit it.
- The fallback list is the safety net for when the import fails; the new test keeps
  it honest. When anyone edits `HARD_BLOCKLIST_PATHS`, the test will fail until the
  fallback is updated too — that's intended.
- Layer 1 is advisory (a prompt); layers 2/3 (`test_gate.validate_diff`) are the
  enforced truth. This plan does not change enforcement — it restores layer-1
  fidelity and burns fewer build slots on doomed `blocked_path` proposals.
