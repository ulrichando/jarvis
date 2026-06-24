# Pre-TTS Confab Gate + Specialty Model Routing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Block confabulated supervisor replies before TTS streams them aloud, and route specialty-routed TASK subtypes (DESKTOP / BROWSER / CODE / FILES / OTHER) to their best-fit models across the user's 5 provider families (Claude / ChatGPT / Gemini / Kimi / DeepSeek). On gate trip, retry with a tool-forcing system message; escalate up a per-route model tier before voicing a safe filler.

**Architecture:** Extend `pipeline/turn_router.py` with sub-route labels. Add `pipeline/specialty_routes.py` (new) — table mapping `Route → primary + 3-tier retry ladder`. Add `pipeline/pre_tts_confab_gate.py` (new) — gate logic + retry orchestration. Wire the gate into `jarvis_agent.py`'s `on_speech_committed`-equivalent path. Telemetry columns added via online `ALTER TABLE` migrations. The supervisor stays single-LLM-per-turn — NO subagent layer restored.

**Tech Stack:** Python 3.13, livekit-agents 1.5.9, anthropic + openai + groq + deepseek + gemini SDKs (all already in `requirements.txt`), pytest with asyncio mode, sqlite3 for telemetry.

---

## Operational guardrails (read before starting any task)

- **Restart caution.** Before any `systemctl --user restart jarvis-voice-agent.service`, check `~/.local/share/jarvis/turn_telemetry.db` for `ts_utc` in the last 60s. If active, ask user.
- **No Co-Authored-By / Claude attribution** in any commit body. Ever.
- **No subagent terminology.** Naming is "specialty routes" / "route handlers". Per `.claude/rules/voice-agent.md`.
- **Stage specific files only.** Never `git add .` / `git add -A`. The user has unrelated WIP.
- **One file path per spec.** Don't restructure surrounding code.
- **Voice-agent has its own venv.** Tests run via `cd src/voice-agent && .venv/bin/python -m pytest tests/`.
- **`looks_like_completion_claim` already exists** at `src/voice-agent/confab_detector.py` (landed in commit `976749de`). Tasks below use it directly — do NOT redefine.

---

## File map

**Create (3 files):**
- `src/voice-agent/pipeline/specialty_routes.py` — dispatch table + lookup helpers (~120 lines)
- `src/voice-agent/pipeline/pre_tts_confab_gate.py` — gate + retry orchestration (~180 lines)
- `src/voice-agent/tests/test_pre_tts_confab_gate.py` — gate-fire matrix + retry chain (~200 lines)
- `src/voice-agent/tests/test_specialty_routes.py` — sub-route dispatch + env override (~120 lines)

**Modify (5 files):**
- `src/voice-agent/pipeline/turn_router.py` — extend Route enum, classifier prompt, `_ROUTE_BASE` (+50 lines)
- `src/voice-agent/pipeline/turn_telemetry.py` — new columns + expanded `confab_check_state` values (+25 lines)
- `src/voice-agent/providers/llm.py` — extend `build_dispatching_llm` to consult specialty_routes (+40 lines)
- `src/voice-agent/jarvis_agent.py` — wire gate into reply path + front-loaded ack (+50 lines)
- `src/voice-agent/confab_detector.py` — already done (commit `976749de`) — no changes needed

Total new code: ~810 LOC including tests.

---

## Task 1: Telemetry migration — new columns + expanded state values

**Files:**
- Modify: `src/voice-agent/pipeline/turn_telemetry.py` (around line 248, right after the existing `confab_check_state` migration block)

**What this does:** Adds two new `turns` columns (`confab_pattern_matched`, `confab_retry_models`) via idempotent `ALTER TABLE ADD COLUMN`. The expanded `confab_check_state` value set lives in convention (no schema enforcement — it's a TEXT column already), but we add a constants module documenting the new values for type-checkers and tests.

### Sub-tasks

- [ ] **Step 1.1: Read the existing migration pattern**

```bash
sed -n '230,260p' /home/ulrich/Documents/Projects/jarvis/src/voice-agent/pipeline/turn_telemetry.py
```

Confirm the pattern: `PRAGMA table_info(turns)` reads existing columns, then conditionally `ALTER TABLE ADD COLUMN` inside a try/except. Idempotent on re-run.

- [ ] **Step 1.2: Add the two new migration blocks**

In `src/voice-agent/pipeline/turn_telemetry.py`, find the `confab_check_state` migration block at line ~241. Directly AFTER that block (before the AEC cascade block at line ~248), add:

```python
        # 2026-05-24 — pre-TTS confab gate observability columns.
        # confab_pattern_matched: which _STRONG_CLAIMS regex source string
        # fired the gate (e.g. r"\b(?:chrome|firefox|...|open|launched|running)\b").
        # confab_retry_models: JSON list of model ids tried in order, ending
        # with the model whose reply was voiced (or empty when gate didn't
        # trip). Both NULL when JARVIS_PRE_TTS_CONFAB_GATE=0 / gate bypass.
        # Spec: docs/superpowers/specs/2026-05-24-pre-tts-confab-gate-design.md
        gate_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(turns)")
        }
        for col, decl in (
            ("confab_pattern_matched", "TEXT"),
            ("confab_retry_models",    "TEXT"),
        ):
            if col not in gate_cols:
                try:
                    conn.execute(
                        f"ALTER TABLE turns ADD COLUMN {col} {decl}"
                    )
                except sqlite3.OperationalError:
                    pass
```

- [ ] **Step 1.3: Add the value-set constants**

At the top of `src/voice-agent/pipeline/turn_telemetry.py` (right after the imports), add:

```python
# Pre-TTS confab gate state values (2026-05-24).
# Stored in turns.confab_check_state as TEXT (no schema enforcement).
# Convention only; documented for type-checkers + tests.
CONFAB_STATE_CLEAN              = "clean"
CONFAB_STATE_CAUGHT_T1_PASSED   = "caught_t1_passed"
CONFAB_STATE_CAUGHT_T2_PASSED   = "caught_t2_passed"
CONFAB_STATE_CAUGHT_T3_PASSED   = "caught_t3_passed"
CONFAB_STATE_CAUGHT_FILLER      = "caught_filler"
CONFAB_STATE_BYPASSED_KILLED    = "bypassed_killed"
```

Find a sensible spot — after the file-level imports but before any function definition.

- [ ] **Step 1.4: Write a migration test**

Create `src/voice-agent/tests/test_telemetry_migration_pre_tts_gate.py`:

```python
"""Migration test for the pre-TTS confab gate telemetry columns
(2026-05-24). Verifies init_db is idempotent and the two new columns
are added cleanly to an existing database."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest


def _columns(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {r[1] for r in conn.execute("PRAGMA table_info(turns)")}


def test_init_db_adds_pre_tts_gate_columns():
    from pipeline.turn_telemetry import init_db
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.db"
        init_db(db)
        cols = _columns(db)
        assert "confab_pattern_matched" in cols
        assert "confab_retry_models" in cols


def test_init_db_idempotent():
    """Running init_db twice must not raise (ALTER guarded by IF NOT
    EXISTS pattern)."""
    from pipeline.turn_telemetry import init_db
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "t.db"
        init_db(db)
        init_db(db)  # second call — must not raise
        cols = _columns(db)
        assert "confab_pattern_matched" in cols


def test_state_constants_exported():
    """The CONFAB_STATE_* constants are part of the module's public surface."""
    import pipeline.turn_telemetry as tt
    assert tt.CONFAB_STATE_CLEAN == "clean"
    assert tt.CONFAB_STATE_CAUGHT_T1_PASSED == "caught_t1_passed"
    assert tt.CONFAB_STATE_CAUGHT_T2_PASSED == "caught_t2_passed"
    assert tt.CONFAB_STATE_CAUGHT_T3_PASSED == "caught_t3_passed"
    assert tt.CONFAB_STATE_CAUGHT_FILLER == "caught_filler"
    assert tt.CONFAB_STATE_BYPASSED_KILLED == "bypassed_killed"
```

- [ ] **Step 1.5: Run the test**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_telemetry_migration_pre_tts_gate.py -v
```

Expected: 3 passed.

- [ ] **Step 1.6: Apply the migration to the live database**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -c "from pipeline.turn_telemetry import init_db; init_db()"
```

Then verify:

```bash
sqlite3 ~/.local/share/jarvis/turn_telemetry.db ".schema turns" | grep -oE "confab_(pattern_matched|retry_models)"
```

Expected output: both column names listed.

- [ ] **Step 1.7: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/pipeline/turn_telemetry.py src/voice-agent/tests/test_telemetry_migration_pre_tts_gate.py
git commit -m "$(cat <<'EOF'
feat(telemetry): pre-TTS confab gate columns + state constants

Add confab_pattern_matched + confab_retry_models TEXT columns to
turns table via idempotent ALTER TABLE ADD COLUMN. Add module-level
CONFAB_STATE_* constants for the expanded confab_check_state value
set the gate will write.

Pre-TTS gate logic itself lands in subsequent commits — this is
schema only, no behavior change.
EOF
)"
```

---

## Task 2: Sub-route classifier — extend Route enum + classifier prompt

**Files:**
- Modify: `src/voice-agent/pipeline/turn_router.py` (lines 15, 282-348, the `classify_turn` function)

**What this does:** Splits `TASK` into 5 sub-routes (`TASK_DESKTOP` / `TASK_BROWSER` / `TASK_CODE` / `TASK_FILES` / `TASK_OTHER`). Updates the `Route` literal, the `_VALID_ROUTES` set, the `_ROUTE_BASE` (min_words/min_duration) table, the classifier system prompt, and `route_from_classifier_output`. Callers that branch on `route == "TASK"` get updated to `route.startswith("TASK_")` — search the codebase.

### Sub-tasks

- [ ] **Step 2.1: Read current Route + _ROUTE_BASE**

```bash
sed -n '15,15p;280,350p' /home/ulrich/Documents/Projects/jarvis/src/voice-agent/pipeline/turn_router.py
```

Confirm: `Route = Literal[...]` at line 15, `_VALID_ROUTES` at line 282, `_ROUTE_BASE` table at line 319.

- [ ] **Step 2.2: Find all `route == "TASK"` and `route.startswith` callers**

```bash
grep -rn '"TASK"\|route == "TASK"\|in ("TASK"\|route.startswith' /home/ulrich/Documents/Projects/jarvis/src/voice-agent --include="*.py" | grep -v __pycache__ | head -30
```

Note each location — these become `route.startswith("TASK_")` after the split (except in places that need to differentiate sub-routes, like the new dispatcher in Task 4).

- [ ] **Step 2.3: Update the Route literal + _VALID_ROUTES**

In `src/voice-agent/pipeline/turn_router.py`, replace:

```python
Route   = Literal["BANTER", "TASK", "REASONING", "EMOTIONAL"]
```

with:

```python
Route = Literal[
    "BANTER",
    "TASK_DESKTOP",
    "TASK_BROWSER",
    "TASK_CODE",
    "TASK_FILES",
    "TASK_OTHER",
    "REASONING",
    "EMOTIONAL",
]
```

Then find `_VALID_ROUTES = {"BANTER", "TASK", "REASONING", "EMOTIONAL"}` at line ~282 and replace with:

```python
_VALID_ROUTES = {
    "BANTER",
    "TASK_DESKTOP", "TASK_BROWSER", "TASK_CODE", "TASK_FILES", "TASK_OTHER",
    "REASONING", "EMOTIONAL",
}
```

- [ ] **Step 2.4: Update `_ROUTE_BASE`**

Find `_ROUTE_BASE = { ... }` at line ~319. Replace the `"TASK"` entry with 5 entries (same min_words/min_duration values, just cloned per sub-route):

```python
_ROUTE_BASE = {
    "BANTER":       (0, 0.3),
    "TASK_DESKTOP": (0, 0.4),
    "TASK_BROWSER": (0, 0.4),
    "TASK_CODE":    (0, 0.4),
    "TASK_FILES":   (0, 0.4),
    "TASK_OTHER":   (0, 0.4),
    "REASONING":    (0, 0.5),
    "EMOTIONAL":    (0, 0.6),
}
```

- [ ] **Step 2.5: Update the classifier prompt**

Find the classifier system prompt around lines 289-295 (the docstring-like block with `BANTER — chitchat, jokes, idle conversation` etc.). Replace with the new label set:

```python
"""Classify the user's voice turn into one of 8 routes:

  BANTER        — chitchat, jokes, idle conversation, single-word
                  acknowledgements ("yeah", "ok", "thanks")
  TASK_DESKTOP  — clicks, screenshots, "look at my screen",
                  GUI work, app launches ("open Chrome"),
                  minimized-window work, any visible-desktop request
  TASK_BROWSER  — "navigate to X", "search the web for Y", "open
                  the Wikipedia page for Z", visible browser actions
  TASK_CODE     — write / fix / refactor code, run a script, debug
                  a stack trace, work with a code file
  TASK_FILES    — read / edit / grep / patch files (no execution),
                  "show me line N of foo.py"
  TASK_OTHER    — fact lookup, web_fetch, memory ops, schedule, todo,
                  vuln_check, anything that doesn't fit a sub-route above
  REASONING     — multi-step thinking, planning, long-form debugging,
                  "what's the best way to X"
  EMOTIONAL     — feelings, support, hard decisions, frustration

Respond with EXACTLY ONE label from the list above. No other text."""
```

(Match the exact docstring shape — the prompt is read at runtime by `classify_turn`.)

- [ ] **Step 2.6: Update `route_from_classifier_output`**

Find `def route_from_classifier_output(raw: str) -> Route:` around line 302. The function strips and uppercases; the new value set just needs the fallback updated. Find the line:

```python
return cleaned if cleaned in _VALID_ROUTES else "TASK"  # type: ignore
```

Replace with:

```python
return cleaned if cleaned in _VALID_ROUTES else "TASK_OTHER"  # type: ignore
```

And the `return "TASK"` fallback above it (line 304):

```python
return "TASK_OTHER"
```

- [ ] **Step 2.7: Update downstream callers that branched on `route == "TASK"`**

From Step 2.2's grep results, update each caller. The pattern is:

```python
# Before:
if route == "TASK":
# After:
if route.startswith("TASK_"):
```

Common locations (verify from your grep — paths may vary):
- `src/voice-agent/jarvis_agent.py` (multiple sites — search for `"TASK"`)
- `src/voice-agent/pipeline/turn_router.py::get_route_thresholds`
- `src/voice-agent/pipeline/turn_graph.py` (if exists)
- `src/voice-agent/pipeline/skill_review.py::is_hard_turn` — already updated, but verify it uses the new label space (it uses `in ("TASK", "REASONING")` which becomes `in ("REASONING",) or route.startswith("TASK_")`).

For `skill_review.py::is_hard_turn`, the change is:

```python
# Before:
if snapshot.route in ("TASK", "REASONING") and len(snapshot.jarvis_text or "") >= _long_reply_chars():
    return True
# (and the confab-shape branch below also has the same check)
# After:
if (snapshot.route == "REASONING" or snapshot.route.startswith("TASK_")) and len(snapshot.jarvis_text or "") >= _long_reply_chars():
    return True
```

Apply consistently across BOTH the long-reply branch and the confab-shape branch.

- [ ] **Step 2.8: Write tests for the new label space**

Create `src/voice-agent/tests/test_specialty_routes_classifier.py`:

```python
"""Tests for the extended Route label set (2026-05-24)."""
from __future__ import annotations

import pytest

from pipeline.turn_router import (
    Route,
    _VALID_ROUTES,
    _ROUTE_BASE,
    route_from_classifier_output,
)


def test_all_8_routes_in_valid_set():
    expected = {
        "BANTER",
        "TASK_DESKTOP", "TASK_BROWSER", "TASK_CODE", "TASK_FILES", "TASK_OTHER",
        "REASONING", "EMOTIONAL",
    }
    assert _VALID_ROUTES == expected


def test_route_base_covers_all_routes():
    for r in _VALID_ROUTES:
        assert r in _ROUTE_BASE, f"{r} missing from _ROUTE_BASE"


def test_route_from_output_recognizes_sub_routes():
    assert route_from_classifier_output("TASK_DESKTOP") == "TASK_DESKTOP"
    assert route_from_classifier_output("TASK_CODE") == "TASK_CODE"
    assert route_from_classifier_output("task_browser") == "TASK_BROWSER"  # case-insensitive


def test_route_from_output_unknown_falls_back_to_task_other():
    """Pre-2026-05-24 the fallback was 'TASK'; now it's TASK_OTHER."""
    assert route_from_classifier_output("BOGUS") == "TASK_OTHER"
    assert route_from_classifier_output("") == "TASK_OTHER"


def test_legacy_task_label_no_longer_accepted_falls_to_other():
    """A classifier emitting bare 'TASK' (old label) gets normalized."""
    assert route_from_classifier_output("TASK") == "TASK_OTHER"
```

- [ ] **Step 2.9: Run the new tests + the full suite**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_specialty_routes_classifier.py -v
.venv/bin/python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: 5/5 new tests pass. Full suite has only the pre-existing HONCHO failures.

If `is_hard_turn` tests fail because of the label change, update `test_is_hard_turn_confab.py` and `test_self_improve_wiring.py` to use the new sub-route labels (e.g. `route="TASK_DESKTOP"` instead of `route="TASK"`).

- [ ] **Step 2.10: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/pipeline/turn_router.py src/voice-agent/pipeline/skill_review.py src/voice-agent/jarvis_agent.py src/voice-agent/tests/test_specialty_routes_classifier.py
# Also include any tests you had to update:
git add -u src/voice-agent/tests/
git commit -m "$(cat <<'EOF'
feat(turn-router): extend Route with 5 TASK sub-routes

Split TASK → TASK_DESKTOP / TASK_BROWSER / TASK_CODE / TASK_FILES /
TASK_OTHER. The classifier system prompt now sub-classifies action
turns; route_from_classifier_output falls back to TASK_OTHER instead
of bare TASK. _ROUTE_BASE clones the prior TASK row across all 5
sub-routes (same min_words=0, min_duration=0.4).

Downstream callers that branched on `route == "TASK"` updated to
`route.startswith("TASK_")` — covers jarvis_agent, skill_review's
is_hard_turn (both branches), and any other consumer.

Specialty model dispatch + pre-TTS gate land in subsequent commits;
this commit only widens the label space. Old behavior preserved:
every sub-route still resolves to the same model (claude-haiku-4-5)
in the dispatcher until Task 4 lands.
EOF
)"
```

---

## Task 3: Specialty routes dispatch table

**Files:**
- Create: `src/voice-agent/pipeline/specialty_routes.py`
- Create: `src/voice-agent/tests/test_specialty_routes.py`

**What this does:** Defines the per-route model assignments (primary + retry ladder) per the spec. Env-overridable via `JARVIS_<ROUTE>_MODEL`. Pure data + lookups — no LLM construction here; Task 4 wires the dispatcher.

### Sub-tasks

- [ ] **Step 3.1: Create the dispatch table file**

Create `src/voice-agent/pipeline/specialty_routes.py`:

```python
"""Specialty routes dispatch — per-route model + retry-ladder table.

Spec: docs/superpowers/specs/2026-05-24-pre-tts-confab-gate-design.md §
"Model assignment per sub-route"

This module is PURE data + lookups. It returns model IDs (strings)
keyed by route + tier. The provider construction (LLM instances,
FallbackAdapter chains) happens in providers/llm.py — which consults
this module to pick the right model for each route.

Each route has 4 tiers:
  tier 0 — primary model (the default for that route)
  tier 1 — same model + tool-forcing system message (retry path)
  tier 2 — escalation to a more capable model
  tier 3 — cross-provider safety net

For BANTER and EMOTIONAL, only tier 0 is defined — those routes
never go through the confab retry chain.

Env overrides (operator tuning without code edits):
  JARVIS_TASK_DESKTOP_MODEL   (default claude-sonnet-4-6)
  JARVIS_TASK_BROWSER_MODEL   (default claude-sonnet-4-6)
  JARVIS_TASK_CODE_MODEL      (default deepseek-v4-flash)
  JARVIS_TASK_FILES_MODEL     (default claude-haiku-4-5)
  JARVIS_TASK_OTHER_MODEL     (default claude-haiku-4-5)
  JARVIS_BANTER_MODEL         (default claude-haiku-4-5; existing)
  JARVIS_REASONING_MODEL      (default claude-sonnet-4-6; existing)
  JARVIS_EMOTIONAL_MODEL      (default claude-haiku-4-5; existing)

The Kimi K2.6 entry for TASK_BROWSER tier-2 is suppressed unless
JARVIS_KIMI_VOICE_EXPERIMENTAL=1 (the K2.6 voice supervisor is
currently broken — 'web_search not in request.tools'). When
suppressed, the slot is None and the retry chain skips to tier 3.
"""
from __future__ import annotations

import os
from typing import Optional

# Tier labels for clarity in callers.
TIER_PRIMARY        = "primary"
TIER_RETRY          = "retry"
TIER_ESCALATE       = "escalate"
TIER_CROSS_PROVIDER = "cross_provider"
TIERS = (TIER_PRIMARY, TIER_RETRY, TIER_ESCALATE, TIER_CROSS_PROVIDER)

# Default ladder per route. The retry tier (tier 1) is conceptually the
# same model as the primary — the difference is the tool-forcing system
# message appended for that call. We model it as "same string" here and
# let the gate orchestration know to use the tool-force prompt on retry.
_DEFAULTS: dict[str, dict[str, Optional[str]]] = {
    "TASK_DESKTOP": {
        TIER_PRIMARY:        "claude-sonnet-4-6",
        TIER_RETRY:          "claude-sonnet-4-6",  # same model + force prompt
        TIER_ESCALATE:       "claude-opus-4-7",
        TIER_CROSS_PROVIDER: "gpt-5.1",
    },
    "TASK_BROWSER": {
        TIER_PRIMARY:        "claude-sonnet-4-6",
        TIER_RETRY:          "claude-sonnet-4-6",
        # Kimi K2.6 here when JARVIS_KIMI_VOICE_EXPERIMENTAL=1 — handled
        # via lookup-time env check in get_route_ladder().
        TIER_ESCALATE:       "claude-opus-4-7",
        TIER_CROSS_PROVIDER: "gpt-5.1",
    },
    "TASK_CODE": {
        TIER_PRIMARY:        "deepseek-v4-flash",
        TIER_RETRY:          "deepseek-v4-flash",
        TIER_ESCALATE:       "claude-sonnet-4-6",
        TIER_CROSS_PROVIDER: "gpt-5.1",
    },
    "TASK_FILES": {
        TIER_PRIMARY:        "claude-haiku-4-5",
        TIER_RETRY:          "claude-haiku-4-5",
        TIER_ESCALATE:       "claude-sonnet-4-6",
        TIER_CROSS_PROVIDER: "deepseek-v4-flash",
    },
    "TASK_OTHER": {
        TIER_PRIMARY:        "claude-haiku-4-5",
        TIER_RETRY:          "claude-haiku-4-5",
        TIER_ESCALATE:       "claude-sonnet-4-6",
        TIER_CROSS_PROVIDER: "gpt-5-mini",
    },
    "BANTER": {
        TIER_PRIMARY:        "claude-haiku-4-5",
        TIER_RETRY:          None,
        TIER_ESCALATE:       None,
        TIER_CROSS_PROVIDER: None,
    },
    "REASONING": {
        TIER_PRIMARY:        "claude-sonnet-4-6",
        TIER_RETRY:          "claude-sonnet-4-6",
        TIER_ESCALATE:       "claude-opus-4-7",
        TIER_CROSS_PROVIDER: "gemini-2.5-pro",
    },
    "EMOTIONAL": {
        TIER_PRIMARY:        "claude-haiku-4-5",
        TIER_RETRY:          None,
        TIER_ESCALATE:       None,
        TIER_CROSS_PROVIDER: None,
    },
}

# Env var name for each route's primary override.
_PRIMARY_ENV = {
    "TASK_DESKTOP": "JARVIS_TASK_DESKTOP_MODEL",
    "TASK_BROWSER": "JARVIS_TASK_BROWSER_MODEL",
    "TASK_CODE":    "JARVIS_TASK_CODE_MODEL",
    "TASK_FILES":   "JARVIS_TASK_FILES_MODEL",
    "TASK_OTHER":   "JARVIS_TASK_OTHER_MODEL",
    "BANTER":       "JARVIS_BANTER_MODEL",
    "REASONING":    "JARVIS_REASONING_MODEL",
    "EMOTIONAL":    "JARVIS_EMOTIONAL_MODEL",
}


def get_primary_model(route: str) -> Optional[str]:
    """Return the route's primary model id, honoring env override."""
    env = _PRIMARY_ENV.get(route)
    if env:
        override = os.environ.get(env, "").strip()
        if override:
            return override
    return _DEFAULTS.get(route, {}).get(TIER_PRIMARY)


def get_route_ladder(route: str) -> list[Optional[str]]:
    """Return the 4-tier ladder for a route, in order:
    [primary, retry, escalate, cross_provider].

    Env override applies to the primary slot AND propagates to the retry
    slot (since retry is conceptually the same model + force prompt).
    Kimi K2.6 substitution for TASK_BROWSER tier-2 (escalate) is honored
    when JARVIS_KIMI_VOICE_EXPERIMENTAL=1."""
    if route not in _DEFAULTS:
        return [None, None, None, None]

    primary = get_primary_model(route)
    defaults = _DEFAULTS[route]

    # Retry slot tracks the primary (env override flows through).
    retry = primary if defaults[TIER_RETRY] is not None else None

    escalate = defaults[TIER_ESCALATE]
    # Kimi substitution: only TASK_BROWSER, only when experimental flag set.
    if route == "TASK_BROWSER" and os.environ.get(
        "JARVIS_KIMI_VOICE_EXPERIMENTAL", "0"
    ) == "1":
        escalate = "moonshotai/kimi-k2"  # K2.6 entry; matches SPEECH_MODELS id

    cross = defaults[TIER_CROSS_PROVIDER]

    return [primary, retry, escalate, cross]


def routes_with_retry_chain() -> set[str]:
    """Routes whose ladder has at least one non-None retry tier
    (i.e. routes that participate in the pre-TTS confab gate's retry
    chain). BANTER + EMOTIONAL are excluded — gate bypasses them."""
    out = set()
    for route, table in _DEFAULTS.items():
        for tier in (TIER_RETRY, TIER_ESCALATE, TIER_CROSS_PROVIDER):
            if table.get(tier) is not None:
                out.add(route)
                break
    return out
```

- [ ] **Step 3.2: Create the tests**

Create `src/voice-agent/tests/test_specialty_routes.py`:

```python
"""Tests for pipeline.specialty_routes — model dispatch table + lookups."""
from __future__ import annotations

import os
import unittest.mock as mock

import pytest

from pipeline.specialty_routes import (
    TIER_PRIMARY, TIER_RETRY, TIER_ESCALATE, TIER_CROSS_PROVIDER,
    get_primary_model,
    get_route_ladder,
    routes_with_retry_chain,
    _DEFAULTS,
)


def test_all_8_routes_have_a_primary():
    for r in (
        "BANTER",
        "TASK_DESKTOP", "TASK_BROWSER", "TASK_CODE", "TASK_FILES", "TASK_OTHER",
        "REASONING", "EMOTIONAL",
    ):
        assert get_primary_model(r) is not None, f"{r} missing primary"


def test_task_desktop_primary_is_sonnet():
    assert get_primary_model("TASK_DESKTOP") == "claude-sonnet-4-6"


def test_task_code_primary_is_deepseek():
    assert get_primary_model("TASK_CODE") == "deepseek-v4-flash"


def test_task_files_primary_is_haiku():
    assert get_primary_model("TASK_FILES") == "claude-haiku-4-5"


def test_env_override_swaps_primary():
    with mock.patch.dict(os.environ, {"JARVIS_TASK_DESKTOP_MODEL": "claude-opus-4-7"}):
        assert get_primary_model("TASK_DESKTOP") == "claude-opus-4-7"


def test_env_override_blank_string_falls_back_to_default():
    with mock.patch.dict(os.environ, {"JARVIS_TASK_DESKTOP_MODEL": "   "}):
        assert get_primary_model("TASK_DESKTOP") == "claude-sonnet-4-6"


def test_get_ladder_returns_four_tiers():
    ladder = get_route_ladder("TASK_DESKTOP")
    assert len(ladder) == 4
    # primary, retry, escalate, cross_provider
    assert ladder[0] == "claude-sonnet-4-6"
    assert ladder[1] == "claude-sonnet-4-6"  # retry slot tracks primary
    assert ladder[2] == "claude-opus-4-7"
    assert ladder[3] == "gpt-5.1"


def test_banter_ladder_only_primary():
    ladder = get_route_ladder("BANTER")
    assert ladder[0] == "claude-haiku-4-5"
    assert ladder[1] is None  # no retry — gate bypasses BANTER
    assert ladder[2] is None
    assert ladder[3] is None


def test_emotional_ladder_only_primary():
    ladder = get_route_ladder("EMOTIONAL")
    assert ladder[0] == "claude-haiku-4-5"
    assert ladder[1] is None


def test_reasoning_cross_provider_is_gemini():
    ladder = get_route_ladder("REASONING")
    assert ladder[3] == "gemini-2.5-pro"


def test_task_other_cross_provider_is_gpt5_mini():
    ladder = get_route_ladder("TASK_OTHER")
    assert ladder[3] == "gpt-5-mini"


def test_kimi_suppressed_without_experimental_flag():
    # Ensure no leaked env from other tests
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("JARVIS_KIMI_VOICE_EXPERIMENTAL", None)
        ladder = get_route_ladder("TASK_BROWSER")
        # tier 2 (escalate) is Opus, NOT Kimi, when flag is off
        assert ladder[2] == "claude-opus-4-7"


def test_kimi_activates_with_experimental_flag():
    with mock.patch.dict(os.environ, {"JARVIS_KIMI_VOICE_EXPERIMENTAL": "1"}):
        ladder = get_route_ladder("TASK_BROWSER")
        assert ladder[2] == "moonshotai/kimi-k2"


def test_env_override_propagates_to_retry_slot():
    """Retry tier always tracks the primary — env override flows through."""
    with mock.patch.dict(os.environ, {"JARVIS_TASK_CODE_MODEL": "claude-haiku-4-5"}):
        ladder = get_route_ladder("TASK_CODE")
        assert ladder[0] == "claude-haiku-4-5"
        assert ladder[1] == "claude-haiku-4-5"


def test_routes_with_retry_chain_excludes_banter_emotional():
    routes = routes_with_retry_chain()
    assert "BANTER" not in routes
    assert "EMOTIONAL" not in routes
    assert "TASK_DESKTOP" in routes
    assert "REASONING" in routes


def test_unknown_route_returns_empty_ladder():
    ladder = get_route_ladder("BOGUS_ROUTE")
    assert ladder == [None, None, None, None]
```

- [ ] **Step 3.3: Run the tests**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_specialty_routes.py -v
```

Expected: 15 passed.

- [ ] **Step 3.4: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/pipeline/specialty_routes.py src/voice-agent/tests/test_specialty_routes.py
git commit -m "$(cat <<'EOF'
feat(specialty-routes): per-route model + retry ladder table

New pipeline/specialty_routes.py is pure data + lookups: maps each
route to a 4-tier ladder (primary, retry-with-force, escalate,
cross-provider). Models pulled from all 5 provider families per
spec — Claude / OpenAI / Gemini / Kimi / DeepSeek.

  TASK_DESKTOP → Sonnet → Sonnet+force → Opus → GPT-5.1
  TASK_BROWSER → Sonnet → Sonnet+force → Opus (or Kimi gated) → GPT-5.1
  TASK_CODE    → DeepSeek → DeepSeek+force → Sonnet → GPT-5.1
  TASK_FILES   → Haiku → Haiku+force → Sonnet → DeepSeek
  TASK_OTHER   → Haiku → Haiku+force → Sonnet → GPT-5-mini
  REASONING    → Sonnet → Sonnet+force → Opus → Gemini 2.5 Pro

BANTER + EMOTIONAL only define a primary (gate bypasses them).

Env overrides per route (JARVIS_TASK_DESKTOP_MODEL etc.) flow into
both the primary and retry slots. Kimi K2.6 escalate-slot for
TASK_BROWSER suppressed unless JARVIS_KIMI_VOICE_EXPERIMENTAL=1
(K2.6 voice supervisor is broken per CLAUDE.md).

Dispatcher wiring lands in the next commit.
EOF
)"
```

---

## Task 4: Wire dispatcher to consult specialty_routes

**Files:**
- Modify: `src/voice-agent/providers/llm.py` (the `build_dispatching_llm` function, around line 921)

**What this does:** `build_dispatching_llm` currently hardcodes per-route model selection (BANTER → haiku, TASK → haiku, REASONING → sonnet, EMOTIONAL → haiku). After this task, it consults `specialty_routes.get_primary_model(route)` for each of the 8 routes. The fallback chain (Groq + DeepSeek rungs) stays as-is. The `task_override` parameter still applies to all TASK_* sub-routes for tray-pick compatibility.

### Sub-tasks

- [ ] **Step 4.1: Read the existing dispatcher**

```bash
sed -n '921,1050p' /home/ulrich/Documents/Projects/jarvis/src/voice-agent/providers/llm.py
```

Confirm: `build_dispatching_llm(task_override=None)` reads `JARVIS_TASK_MODEL` etc., builds per-route FallbackAdapters, returns a DispatchingLLM. Anthropic primary, Groq legacy, DeepSeek tail.

- [ ] **Step 4.2: Extend the dispatcher signature**

In `src/voice-agent/providers/llm.py`, find:

```python
def build_dispatching_llm(task_override: Optional[Any] = None) -> DispatchingLLM:
```

Replace with:

```python
def build_dispatching_llm(task_override: Optional[Any] = None) -> DispatchingLLM:
    """[existing docstring — preserve, then append the lines below]

    2026-05-24: route model selection now consults
    pipeline.specialty_routes.get_primary_model(route) for the 8-route
    label space (BANTER, TASK_{DESKTOP,BROWSER,CODE,FILES,OTHER},
    REASONING, EMOTIONAL). The legacy JARVIS_TASK_MODEL env var still
    works for backwards compatibility — when set, it overrides ALL
    TASK_* sub-routes (intentional: a tray-pinned model applies to
    all action work regardless of sub-classification). Per-sub-route
    env vars (JARVIS_TASK_DESKTOP_MODEL etc.) win over JARVIS_TASK_MODEL
    when both are set.
    """
```

(Append the new paragraph to the existing docstring; don't replace it wholesale.)

- [ ] **Step 4.3: Import specialty_routes**

Near the top of `providers/llm.py` (where other `from pipeline import ...` lines may exist; if none, add near the standard imports), add:

```python
from pipeline import specialty_routes as _specialty
```

- [ ] **Step 4.4: Replace per-route model selection inside `build_dispatching_llm`**

Find the existing block that selects per-route models (looks something like):

```python
banter_id = os.environ.get("JARVIS_BANTER_MODEL", "claude-haiku-4-5")
task_id   = os.environ.get("JARVIS_TASK_MODEL",   "claude-haiku-4-5")
reasoning_id = os.environ.get("JARVIS_REASONING_MODEL", "claude-sonnet-4-6")
emotional_id = os.environ.get("JARVIS_EMOTIONAL_MODEL", "claude-haiku-4-5")
```

Replace with:

```python
# Legacy JARVIS_TASK_MODEL still works — when set, it applies to ALL
# TASK_* sub-routes (tray-pinned model wins over per-sub-route default).
_legacy_task = os.environ.get("JARVIS_TASK_MODEL", "").strip() or None

def _resolve(route: str) -> str:
    # Per-sub-route env var wins (JARVIS_TASK_DESKTOP_MODEL etc.).
    primary = _specialty.get_primary_model(route)
    if primary and primary != _specialty._DEFAULTS.get(route, {}).get("primary"):
        # The route's own env var was set — use it.
        return primary
    # Otherwise, if legacy JARVIS_TASK_MODEL is set AND this is a TASK_* route,
    # apply it.
    if _legacy_task and route.startswith("TASK_"):
        return _legacy_task
    return primary

banter_id       = _resolve("BANTER")
task_desktop_id = _resolve("TASK_DESKTOP")
task_browser_id = _resolve("TASK_BROWSER")
task_code_id    = _resolve("TASK_CODE")
task_files_id   = _resolve("TASK_FILES")
task_other_id   = _resolve("TASK_OTHER")
reasoning_id    = _resolve("REASONING")
emotional_id    = _resolve("EMOTIONAL")
```

- [ ] **Step 4.5: Update the dispatch map**

The existing code builds a dict keyed by `Route` literal values (e.g. `"BANTER" → llm_instance, "TASK" → llm_instance, ...`). Find that dict (search for `"BANTER":` near the end of `build_dispatching_llm`). Replace the 4-entry dict with 8 entries:

```python
dispatch_map = {
    "BANTER":       _build_route_llm(banter_id,       task_override=None,            ...),
    "TASK_DESKTOP": _build_route_llm(task_desktop_id, task_override=task_override,   ...),
    "TASK_BROWSER": _build_route_llm(task_browser_id, task_override=task_override,   ...),
    "TASK_CODE":    _build_route_llm(task_code_id,    task_override=task_override,   ...),
    "TASK_FILES":   _build_route_llm(task_files_id,   task_override=task_override,   ...),
    "TASK_OTHER":   _build_route_llm(task_other_id,   task_override=task_override,   ...),
    "REASONING":    _build_route_llm(reasoning_id,    task_override=None,            ...),
    "EMOTIONAL":    _build_route_llm(emotional_id,    task_override=None,            ...),
}
```

(Note: `task_override` applies to ALL TASK_* sub-routes — tray-pin propagates across them. Read the existing build helper — if the signature differs, match the existing call shape. The key change is going from 4 entries to 8.)

- [ ] **Step 4.6: Update logging**

If `build_dispatching_llm` has a final `logger.info(f"[dispatch] LLM dispatcher resolved: BANTER=..., TASK=..., REASONING=..., EMOTIONAL=...")` line, expand it to log all 8 routes. Mirror the existing format.

- [ ] **Step 4.7: Write integration tests**

Create `src/voice-agent/tests/test_dispatcher_specialty_routes.py`:

```python
"""Integration tests for build_dispatching_llm + specialty_routes wiring."""
from __future__ import annotations

import os
import unittest.mock as mock

import pytest


def test_dispatcher_has_all_8_routes():
    from providers.llm import build_dispatching_llm
    disp = build_dispatching_llm()
    routes = set(getattr(disp, "route_to_llm", {}).keys())
    # The route_to_llm attribute name may differ — adapt if the
    # DispatchingLLM exposes routes under a different name. Inspect
    # the class definition if this fails.
    for expected in (
        "BANTER",
        "TASK_DESKTOP", "TASK_BROWSER", "TASK_CODE", "TASK_FILES", "TASK_OTHER",
        "REASONING", "EMOTIONAL",
    ):
        assert expected in routes, f"{expected} missing from dispatcher"


def test_legacy_task_model_applies_to_all_task_sub_routes():
    """Setting JARVIS_TASK_MODEL must override every TASK_* primary
    (preserves tray-pinned-model behavior for the pre-2026-05-24
    label-space callers)."""
    with mock.patch.dict(os.environ, {"JARVIS_TASK_MODEL": "claude-opus-4-7"}):
        from importlib import reload
        from providers import llm
        reload(llm)
        # Walk the dispatcher's route map and assert every TASK_* slot
        # resolves to claude-opus-4-7. Exact attribute path depends on
        # the DispatchingLLM internals — inspect at test time.


def test_per_sub_route_env_wins_over_legacy():
    with mock.patch.dict(os.environ, {
        "JARVIS_TASK_MODEL":         "claude-opus-4-7",
        "JARVIS_TASK_DESKTOP_MODEL": "claude-haiku-4-5",  # wins for DESKTOP only
    }):
        from pipeline.specialty_routes import get_primary_model
        assert get_primary_model("TASK_DESKTOP") == "claude-haiku-4-5"
        # TASK_BROWSER falls back to legacy JARVIS_TASK_MODEL... actually
        # this falls through get_primary_model first which returns the
        # default (Sonnet). The legacy override happens INSIDE _resolve
        # at dispatch construction. So this test focuses on specialty_routes.
```

(The second and third tests inspect dispatcher internals which may need adjustment when the DispatchingLLM shape is examined. The first test is the load-bearing assertion: the dispatcher exposes 8 routes after this task.)

- [ ] **Step 4.8: Run tests**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_dispatcher_specialty_routes.py tests/test_specialty_routes.py -v
```

Expected: all pass. If the introspection-based test (`test_dispatcher_has_all_8_routes`) fails because `route_to_llm` is the wrong attribute name, read `providers/llm.py` for the DispatchingLLM class definition and update the test to use the right introspection path.

Also run the full suite to check for regressions:

```bash
.venv/bin/python -m pytest tests/ -q 2>&1 | tail -5
```

- [ ] **Step 4.9: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/providers/llm.py src/voice-agent/tests/test_dispatcher_specialty_routes.py
git commit -m "$(cat <<'EOF'
feat(dispatcher): build_dispatching_llm consults specialty_routes

The dispatcher now resolves each of the 8 routes through
pipeline.specialty_routes.get_primary_model, falling through:
  per-sub-route env var (JARVIS_TASK_DESKTOP_MODEL etc.) → default

The legacy JARVIS_TASK_MODEL is preserved as a backwards-compat
override that propagates across all TASK_* sub-routes (tray-pinned
model behavior). Per-sub-route env wins when both are set.

After this commit:
  TASK_DESKTOP / TASK_BROWSER → claude-sonnet-4-6 (was Haiku!)
  TASK_CODE                   → deepseek-v4-flash
  TASK_FILES / TASK_OTHER     → claude-haiku-4-5 (unchanged)
  BANTER / EMOTIONAL          → claude-haiku-4-5 (unchanged)
  REASONING                   → claude-sonnet-4-6 (unchanged)

The pre-TTS gate (next commit) consumes the retry-tier slots of the
ladder when it trips.
EOF
)"
```

---

## Task 5: Pre-TTS confab gate logic

**Files:**
- Create: `src/voice-agent/pipeline/pre_tts_confab_gate.py`
- Create: `src/voice-agent/tests/test_pre_tts_confab_gate.py`

**What this does:** The gate function `should_gate(route, text, tool_calls)` returns a verdict. The retry orchestrator `run_retry_chain(route, original_chat_ctx, original_tool_calls, original_text, llm_factory)` walks the route's ladder, appending a tool-forcing system message at each retry, returning a `RetryResult` with the final text, the tier that succeeded, and the model id whose reply will be voiced.

### Sub-tasks

- [ ] **Step 5.1: Create the gate module**

Create `src/voice-agent/pipeline/pre_tts_confab_gate.py`:

```python
"""Pre-TTS confab gate — inspect supervisor reply before TTS streams.

Spec: docs/superpowers/specs/2026-05-24-pre-tts-confab-gate-design.md

The gate fires when ALL hold:
  1. route is TASK_* or REASONING (BANTER + EMOTIONAL bypass)
  2. response text matches confab_detector._STRONG_CLAIMS via
     looks_like_completion_claim (added 2026-05-24)
  3. this turn's tool_calls list is EMPTY (no tool fired)
  4. no _NEGATION_PATTERNS in the text (handled by
     looks_like_completion_claim)

On trip, run_retry_chain walks the route's specialty-routes ladder
appending a tool-forcing system message. Returns RetryResult with:
  text: str               — final reply text (voiced via TTS)
  tier_passed: str|None   — which tier produced clean text
                            ("primary"/"retry"/"escalate"/"cross_provider"/None=filler)
  model_id: str           — the model whose reply was voiced
  models_tried: list[str] — chronological list of models tried
  pattern_matched: str|None  — which _STRONG_CLAIMS source string fired

Kill switch: JARVIS_PRE_TTS_CONFAB_GATE=0 disables entirely.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from confab_detector import looks_like_completion_claim
from pipeline import specialty_routes
from pipeline.turn_telemetry import (
    CONFAB_STATE_CLEAN,
    CONFAB_STATE_CAUGHT_T1_PASSED,
    CONFAB_STATE_CAUGHT_T2_PASSED,
    CONFAB_STATE_CAUGHT_T3_PASSED,
    CONFAB_STATE_CAUGHT_FILLER,
    CONFAB_STATE_BYPASSED_KILLED,
)

logger = logging.getLogger("jarvis.pre_tts_gate")

# Routes that bypass the gate entirely (no retry chain).
_BYPASS_ROUTES = ("BANTER", "EMOTIONAL")

# Safe filler voiced when all retries exhaust.
FILLER_TEXT = "I'm having trouble with that — could you try again?"

# Tool-forcing system message appended for retry attempts.
TOOL_FORCE_PROMPT = (
    "Your previous response claimed to have completed an action but "
    "you did not call any tool. The user did not see the action happen. "
    "Call the appropriate tool now — computer_use for desktop work, "
    "browser_task for browsing, terminal for shell — and respond ONLY "
    "after the tool returns. Do not narrate; act."
)


def gate_disabled() -> bool:
    """Master kill switch for the gate. When True, gate is a no-op."""
    return os.environ.get("JARVIS_PRE_TTS_CONFAB_GATE", "1") == "0"


@dataclass
class GateVerdict:
    """Result of the gate's inspection of a completed turn."""
    should_retry: bool
    reason: str
    pattern_matched: Optional[str] = None


def should_gate(
    *,
    route: str,
    text: str,
    tool_calls: list[Any] | None,
) -> GateVerdict:
    """Decide whether THIS completed turn needs a retry.

    Pure function; no I/O. Called by the agent's reply-completion path
    BEFORE TTS streams the text.

    Routes BANTER and EMOTIONAL always bypass — they never make tool
    claims. TASK_* and REASONING are inspected:
      - if tool_calls is non-empty → the LLM actually acted → not a confab
      - if text matches a completion claim AND no tool fired → CONFAB
      - otherwise → clean
    """
    if gate_disabled():
        return GateVerdict(False, "kill_switch")

    if route in _BYPASS_ROUTES:
        return GateVerdict(False, "bypass_route")

    if not route.startswith("TASK_") and route != "REASONING":
        # Unknown route — be permissive (don't gate).
        return GateVerdict(False, "unknown_route")

    if tool_calls:
        return GateVerdict(False, "tool_called")

    looks, pattern = looks_like_completion_claim(text)
    if not looks:
        return GateVerdict(False, "no_claim")

    return GateVerdict(True, "confab_detected", pattern_matched=pattern)


@dataclass
class RetryResult:
    """Outcome of run_retry_chain — the gate's full verdict + retry trace."""
    text: str
    tier_passed: Optional[str]                # None if filler was voiced
    model_id: str                             # model whose text we'll voice
    models_tried: list[str] = field(default_factory=list)
    pattern_matched: Optional[str] = None
    telemetry_state: str = CONFAB_STATE_CLEAN  # one of CONFAB_STATE_*


# Type alias for the LLM factory the agent passes in. Constructs an LLM
# instance for a given model id; returns a callable that takes a chat_ctx
# and tool list, returns (text, tool_calls).
LLMFactory = Callable[[str], "LLMRunner"]
LLMRunner = Callable[[Any, list[Any]], Awaitable[tuple[str, list[Any]]]]


async def run_retry_chain(
    *,
    route: str,
    chat_ctx: Any,
    tool_specs: list[Any],
    original_text: str,
    original_pattern: Optional[str],
    llm_factory: LLMFactory,
) -> RetryResult:
    """Walk the route's ladder. Append TOOL_FORCE_PROMPT to chat_ctx on
    each retry. Returns the first clean reply, or the filler when all
    tiers exhaust.

    The caller passes:
      - chat_ctx: a copy of the supervisor's chat_ctx (mutating allowed)
      - tool_specs: the supervisor's tool schemas (passed through to each LLM)
      - original_text: text from the primary call that tripped the gate
      - original_pattern: the _STRONG_CLAIMS regex source that matched
      - llm_factory: produces an LLM runner for a given model id

    On each retry, chat_ctx gets the tool-force system message
    appended via _append_system_message. The retry returns its own
    (text, tool_calls) — we re-inspect via should_gate. If clean, that
    tier's text is the result. If not, escalate.
    """
    ladder = specialty_routes.get_route_ladder(route)
    tier_names = ("primary", "retry", "escalate", "cross_provider")
    telemetry_states = (
        None,  # tier 0 is the primary — already known to confab; skip
        CONFAB_STATE_CAUGHT_T1_PASSED,
        CONFAB_STATE_CAUGHT_T2_PASSED,
        CONFAB_STATE_CAUGHT_T3_PASSED,
    )

    models_tried: list[str] = [ladder[0]] if ladder[0] else []
    last_text = original_text
    last_pattern = original_pattern

    # Start from tier 1 (retry); tier 0 was the original call that already
    # tripped the gate.
    for tier_idx in range(1, 4):
        model_id = ladder[tier_idx]
        if not model_id:
            continue  # this slot is empty for this route — skip

        models_tried.append(model_id)
        # Append tool-forcing message for this retry attempt. We copy
        # rather than mutate so a future caller can audit chat_ctx after.
        retry_ctx = _append_system_message(chat_ctx, TOOL_FORCE_PROMPT)

        try:
            runner = llm_factory(model_id)
            retry_text, retry_tool_calls = await runner(retry_ctx, tool_specs)
        except Exception as e:
            logger.warning(
                f"[pre_tts_gate] tier={tier_names[tier_idx]} model={model_id} "
                f"raised: {type(e).__name__}: {e}"
            )
            continue

        verdict = should_gate(
            route=route, text=retry_text, tool_calls=retry_tool_calls,
        )
        if not verdict.should_retry:
            # Clean reply — voice it.
            logger.info(
                f"[pre_tts_gate] route={route} tier={tier_names[tier_idx]} "
                f"model={model_id} PASSED ({verdict.reason})"
            )
            return RetryResult(
                text=retry_text,
                tier_passed=tier_names[tier_idx],
                model_id=model_id,
                models_tried=models_tried,
                pattern_matched=original_pattern,
                telemetry_state=telemetry_states[tier_idx],
            )
        # Still confab — note and continue.
        last_text = retry_text
        last_pattern = verdict.pattern_matched or last_pattern
        logger.info(
            f"[pre_tts_gate] route={route} tier={tier_names[tier_idx]} "
            f"model={model_id} STILL CONFAB ({verdict.reason}) — escalating"
        )

    # All tiers exhausted — voice the safe filler.
    logger.warning(
        f"[pre_tts_gate] route={route} ALL TIERS EXHAUSTED — voicing filler. "
        f"models_tried={models_tried}"
    )
    return RetryResult(
        text=FILLER_TEXT,
        tier_passed=None,
        model_id="filler",
        models_tried=models_tried,
        pattern_matched=last_pattern,
        telemetry_state=CONFAB_STATE_CAUGHT_FILLER,
    )


def _append_system_message(chat_ctx: Any, system_text: str) -> Any:
    """Return a shallow copy of chat_ctx with `system_text` appended as
    a system-role message. Defensive about chat_ctx shape — livekit-agents
    ChatContext, plain list, and dict-like all supported.

    The append is a copy so callers can audit the original chat_ctx
    post-retry (the agent's chat_ctx is preserved for the final voiced
    turn)."""
    # livekit-agents ChatContext has a `.copy()` method and `.add_message()`
    # or similar — handle both shapes defensively.
    try:
        copy_fn = getattr(chat_ctx, "copy", None)
        add_fn  = getattr(chat_ctx, "add_message", None)
        if callable(copy_fn) and callable(add_fn):
            new_ctx = copy_fn()
            new_ctx.add_message(role="system", content=system_text)
            return new_ctx
    except Exception:
        pass
    # Plain list fallback.
    if isinstance(chat_ctx, list):
        return chat_ctx + [{"role": "system", "content": system_text}]
    # Unknown shape — best effort: return as-is + log
    logger.warning(
        f"[pre_tts_gate] unknown chat_ctx shape {type(chat_ctx).__name__}; "
        f"tool-force prompt may not have been appended"
    )
    return chat_ctx
```

- [ ] **Step 5.2: Create the gate tests**

Create `src/voice-agent/tests/test_pre_tts_confab_gate.py`:

```python
"""Tests for pipeline.pre_tts_confab_gate — gate-fire matrix + retry chain."""
from __future__ import annotations

import os
import unittest.mock as mock
from dataclasses import dataclass
from typing import Any

import pytest

from pipeline.pre_tts_confab_gate import (
    GateVerdict,
    RetryResult,
    should_gate,
    run_retry_chain,
    gate_disabled,
    FILLER_TEXT,
    TOOL_FORCE_PROMPT,
)
from pipeline.turn_telemetry import (
    CONFAB_STATE_CAUGHT_T1_PASSED,
    CONFAB_STATE_CAUGHT_T2_PASSED,
    CONFAB_STATE_CAUGHT_FILLER,
)


# ── should_gate matrix ──────────────────────────────────────────────

def test_gate_bypasses_banter():
    verdict = should_gate(route="BANTER", text="Chrome is open.", tool_calls=[])
    assert verdict.should_retry is False
    assert verdict.reason == "bypass_route"


def test_gate_bypasses_emotional():
    verdict = should_gate(route="EMOTIONAL", text="Done.", tool_calls=[])
    assert verdict.should_retry is False


def test_gate_clean_when_tool_called():
    """If a tool fired, the claim is legitimate post-tool narration."""
    verdict = should_gate(
        route="TASK_DESKTOP",
        text="Chrome is open.",
        tool_calls=[{"name": "computer_use", "args": {}}],
    )
    assert verdict.should_retry is False
    assert verdict.reason == "tool_called"


def test_gate_clean_when_no_claim():
    """Text doesn't match _STRONG_CLAIMS → no gate trip."""
    verdict = should_gate(
        route="TASK_DESKTOP",
        text="Sure, let me know what to do.",
        tool_calls=[],
    )
    assert verdict.should_retry is False
    assert verdict.reason == "no_claim"


def test_gate_trips_on_task_desktop_chrome_open():
    verdict = should_gate(
        route="TASK_DESKTOP",
        text="Chrome is open. I'll navigate now.",
        tool_calls=[],
    )
    assert verdict.should_retry is True
    assert verdict.reason == "confab_detected"
    assert verdict.pattern_matched is not None


def test_gate_trips_on_task_browser_done_em_dash():
    """The em-dash regex extension catches 'Done — typed X'."""
    verdict = should_gate(
        route="TASK_BROWSER",
        text='Done — typed "anime" in the search bar.',
        tool_calls=[],
    )
    assert verdict.should_retry is True


def test_gate_trips_on_reasoning_claim():
    verdict = should_gate(
        route="REASONING",
        text="Done.",
        tool_calls=[],
    )
    assert verdict.should_retry is True


def test_gate_clean_on_negation():
    """'I can't open Chrome' is negation — no trip."""
    verdict = should_gate(
        route="TASK_DESKTOP",
        text="I can't open Chrome — no display attached.",
        tool_calls=[],
    )
    assert verdict.should_retry is False


def test_gate_disabled_via_env():
    """JARVIS_PRE_TTS_CONFAB_GATE=0 disables entirely."""
    with mock.patch.dict(os.environ, {"JARVIS_PRE_TTS_CONFAB_GATE": "0"}):
        assert gate_disabled() is True
        verdict = should_gate(
            route="TASK_DESKTOP",
            text="Chrome is open.",
            tool_calls=[],
        )
        assert verdict.should_retry is False
        assert verdict.reason == "kill_switch"


def test_gate_enabled_when_env_unset():
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("JARVIS_PRE_TTS_CONFAB_GATE", None)
        assert gate_disabled() is False


# ── run_retry_chain integration ──────────────────────────────────────

@dataclass
class _FakeRunner:
    """Programmable LLM runner for testing the retry chain."""
    reply_per_call: list[tuple[str, list[Any]]]
    calls: list[Any] = None

    def __post_init__(self):
        self.calls = []

    async def __call__(self, chat_ctx: Any, tool_specs: list[Any]):
        self.calls.append((chat_ctx, tool_specs))
        if not self.reply_per_call:
            return ("(no more programmed replies)", [])
        return self.reply_per_call.pop(0)


@pytest.mark.asyncio
async def test_retry_chain_tier1_passes():
    """Primary failed (gate already tripped), tier-1 retry returns clean."""
    runner = _FakeRunner(reply_per_call=[
        ("I've opened Chrome and you can see it.",
         [{"name": "computer_use", "args": {"action": "launch_app", "app": "chrome"}}]),
    ])

    def factory(_model_id: str):
        return runner

    result = await run_retry_chain(
        route="TASK_DESKTOP",
        chat_ctx=[{"role": "user", "content": "open chrome"}],
        tool_specs=[],
        original_text="Chrome is open.",
        original_pattern=r"chrome",
        llm_factory=factory,
    )
    assert result.tier_passed == "retry"
    assert result.text.startswith("I've opened Chrome")
    assert result.telemetry_state == CONFAB_STATE_CAUGHT_T1_PASSED
    assert "claude-sonnet-4-6" in result.models_tried


@pytest.mark.asyncio
async def test_retry_chain_tier1_fails_tier2_passes():
    """Tier-1 still confabs, tier-2 (escalate to Opus) passes."""
    runner_calls_remaining = [
        ("Chrome opened.", []),               # tier 1 still confab
        ("Chrome window now visible.",        # tier 2 with tool
         [{"name": "computer_use", "args": {"action": "launch_app"}}]),
    ]
    runner = _FakeRunner(reply_per_call=runner_calls_remaining)

    def factory(_model_id: str):
        return runner

    result = await run_retry_chain(
        route="TASK_DESKTOP",
        chat_ctx=[{"role": "user", "content": "open chrome"}],
        tool_specs=[],
        original_text="Chrome is open.",
        original_pattern=r"chrome",
        llm_factory=factory,
    )
    assert result.tier_passed == "escalate"
    assert result.telemetry_state == CONFAB_STATE_CAUGHT_T2_PASSED


@pytest.mark.asyncio
async def test_retry_chain_all_tiers_fail_filler_voiced():
    """All 3 retry tiers still confab — filler is voiced."""
    runner = _FakeRunner(reply_per_call=[
        ("Chrome is open.", []),  # tier 1
        ("Done — opened it.", []),  # tier 2
        ("I've opened the browser.", []),  # tier 3
    ])

    def factory(_model_id: str):
        return runner

    result = await run_retry_chain(
        route="TASK_DESKTOP",
        chat_ctx=[{"role": "user", "content": "open chrome"}],
        tool_specs=[],
        original_text="Chrome is open.",
        original_pattern=r"chrome",
        llm_factory=factory,
    )
    assert result.tier_passed is None
    assert result.text == FILLER_TEXT
    assert result.telemetry_state == CONFAB_STATE_CAUGHT_FILLER
    assert result.model_id == "filler"


@pytest.mark.asyncio
async def test_retry_chain_skips_empty_ladder_slots():
    """BANTER's ladder has only tier 0 — retry chain shouldn't make any
    LLM calls when called for a bypass route (but should_gate already
    excludes them; this tests defensively)."""
    runner = _FakeRunner(reply_per_call=[])

    def factory(_model_id: str):
        return runner

    result = await run_retry_chain(
        route="BANTER",  # would never reach here in practice
        chat_ctx=[],
        tool_specs=[],
        original_text="something",
        original_pattern="x",
        llm_factory=factory,
    )
    # All tiers None → filler immediately
    assert result.tier_passed is None
    assert result.text == FILLER_TEXT
    assert len(runner.calls) == 0


@pytest.mark.asyncio
async def test_retry_chain_appends_tool_force_prompt():
    """Each retry attempt appends TOOL_FORCE_PROMPT to chat_ctx."""
    runner = _FakeRunner(reply_per_call=[
        ("Chrome is open.", []),  # still confab
        ("Now actually opening Chrome.",
         [{"name": "computer_use", "args": {}}]),
    ])

    def factory(_model_id: str):
        return runner

    await run_retry_chain(
        route="TASK_DESKTOP",
        chat_ctx=[{"role": "user", "content": "open chrome"}],
        tool_specs=[],
        original_text="Chrome is open.",
        original_pattern=r"chrome",
        llm_factory=factory,
    )

    # Inspect the first retry call's chat_ctx — should contain the tool-force.
    first_ctx, _ = runner.calls[0]
    # chat_ctx may be a list of dicts or a ChatContext — defensive check
    joined = str(first_ctx)
    assert "Your previous response claimed to have completed an action" in joined
```

- [ ] **Step 5.3: Run tests**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -m pytest tests/test_pre_tts_confab_gate.py -v
```

Expected: 16 passed (10 should_gate + 6 retry chain). If `test_retry_chain_appends_tool_force_prompt` fails because chat_ctx serialization is shape-specific, adapt the assertion to match the actual shape (e.g. check via `chat_ctx[-1]["content"]`).

- [ ] **Step 5.4: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/pipeline/pre_tts_confab_gate.py src/voice-agent/tests/test_pre_tts_confab_gate.py
git commit -m "$(cat <<'EOF'
feat(pre-tts-gate): gate + retry-chain orchestration module

New pipeline/pre_tts_confab_gate.py:
  - should_gate(route, text, tool_calls) → GateVerdict
    Pure function. Fires when route is TASK_*/REASONING, text matches
    _STRONG_CLAIMS via looks_like_completion_claim, and tool_calls is
    empty. BANTER + EMOTIONAL always bypass.
  - run_retry_chain(...) → RetryResult
    Walks the route's specialty-routes ladder. Appends TOOL_FORCE_PROMPT
    to chat_ctx at each retry. Returns the first clean reply or the
    safe filler when all tiers exhaust. Telemetry state attached.

Kill switch: JARVIS_PRE_TTS_CONFAB_GATE=0 disables.

No agent wiring yet — that lands in the next commit.
EOF
)"
```

---

## Task 6: Wire gate into the agent

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py` (the reply-completion path; search for where supervisor's text + tool_calls are committed to chat_ctx and TTS is enqueued)

**What this does:** After the LLM completes, intercept BEFORE TTS streams. Call `should_gate`. If clean → pass through to TTS as today. If gate trips → call `run_retry_chain` with the supervisor's chat_ctx + tool specs + a factory closure that builds an LLM runner for any model id. Voice the retry result. Write telemetry (`confab_check_state`, `confab_pattern_matched`, `confab_retry_models`). Also: implement front-loaded ack helper that fires `session.say("One moment.")` after 800ms if the primary LLM is still pending.

### Sub-tasks

- [ ] **Step 6.1: Locate the reply-completion interception point**

```bash
grep -nE "generate_reply|on_speech_committed|on_assistant_committed|conversation_item_added|session.say.*text|tts_text_transforms" /home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py | head -30
```

The interception happens after `generate_reply` returns the full reply but before TTS streams. Identify the specific function or event handler — typical names: `_on_reply_completed`, `_intercept_before_tts`, or a `tts_text_transforms` head.

If the path is not obvious, the safest insertion is at the head of `tts_text_transforms` as a buffered filter — accumulate the streaming text until end-of-stream, then run the gate, then emit either the original or the retry text. This adds latency but matches the spec.

- [ ] **Step 6.2: Build the LLM factory closure**

In `jarvis_agent.py`, inside `entrypoint(ctx)` (where the supervisor LLM stack is constructed), build a factory that returns a callable for any model id:

```python
        # LLM factory for the pre-TTS confab gate's retry chain.
        # Returns an async runner that takes (chat_ctx, tool_specs) and
        # returns (text, tool_calls). Reuses make_speech_llm's
        # SPEECH_MODELS registry — any id therein is constructable.
        from providers.llm import SPEECH_MODELS, _build_route_llm  # _build internal helper
        async def _gate_llm_runner_for(model_id: str):
            """Construct + execute an LLM for one model id."""
            # _build_route_llm constructs a FallbackAdapter around the
            # given model id; same path the dispatcher uses.
            inner_llm = _build_route_llm(model_id, task_override=None, ...)  # match existing call signature
            # Run a single completion. Exact API depends on the LLM's
            # contract — livekit-agents' LLM.chat returns an async
            # iterable of chunks. Aggregate to a single (text, tool_calls).
            chunks = []
            tool_calls: list = []
            async for chunk in inner_llm.chat(chat_ctx=retry_ctx, tools=tool_specs):
                if chunk.content:
                    chunks.append(chunk.content)
                if getattr(chunk, "tool_calls", None):
                    tool_calls.extend(chunk.tool_calls)
            return ("".join(chunks), tool_calls)
        gate_llm_factory = lambda mid: lambda cctx, tspecs: _gate_llm_runner_for(mid)(cctx, tspecs)
```

(The exact LLM chat API may differ — read the existing `make_speech_llm` consumers to find the chunk-iteration pattern. Match it.)

- [ ] **Step 6.3: Wire the gate check**

At the reply-completion interception point (Step 6.1), add:

```python
        # Pre-TTS confab gate (Spec 2026-05-24).
        from pipeline.pre_tts_confab_gate import (
            should_gate,
            run_retry_chain,
            gate_disabled as _gate_disabled,
        )

        if not _gate_disabled():
            verdict = should_gate(
                route=current_route,        # local variable from turn-graph dispatch
                text=reply_text,            # accumulated LLM output
                tool_calls=current_tool_calls,  # tool_calls collected this turn
            )
            if verdict.should_retry:
                logger.warning(
                    f"[pre_tts_gate] route={current_route} TRIPPED "
                    f"pattern={verdict.pattern_matched!r}; running retry chain"
                )
                retry_result = await run_retry_chain(
                    route=current_route,
                    chat_ctx=session.chat_ctx,       # exact attribute may differ
                    tool_specs=current_tool_specs,
                    original_text=reply_text,
                    original_pattern=verdict.pattern_matched,
                    llm_factory=gate_llm_factory,
                )
                reply_text = retry_result.text
                # Record telemetry for this turn.
                _set_turn_telemetry(
                    confab_check_state=retry_result.telemetry_state,
                    confab_pattern_matched=retry_result.pattern_matched,
                    confab_retry_models=json.dumps(retry_result.models_tried),
                )
            else:
                _set_turn_telemetry(confab_check_state="clean")
        else:
            _set_turn_telemetry(confab_check_state="bypassed_killed")
```

(Names like `current_route` / `current_tool_calls` / `current_tool_specs` / `_set_turn_telemetry` need to match what the agent uses in this scope — adapt from the surrounding code.)

- [ ] **Step 6.4: Implement the front-loaded ack helper**

After the gate trip path is wired, add an 800ms ack helper that fires when the primary LLM completion takes too long. The flow:

1. When generate_reply starts, start an asyncio task that sleeps 800ms then fires `session.say("One moment.")` if the primary hasn't returned yet.
2. When the primary returns (clean or trip), cancel the ack task if it hasn't fired.

```python
        # Front-loaded ack — fires 800ms after generate_reply starts if
        # the LLM hasn't returned yet. Cancelled when the primary settles.
        ack_fired = asyncio.Event()
        async def _front_loaded_ack():
            try:
                await asyncio.sleep(0.8)
                if not ack_fired.is_set():
                    session.say("One moment.")
            except asyncio.CancelledError:
                pass
        ack_task = asyncio.create_task(_front_loaded_ack())
        try:
            # ... existing generate_reply call ...
            pass
        finally:
            ack_fired.set()
            ack_task.cancel()
```

Place this around the `generate_reply` invocation in the supervisor's reply flow. If the existing path is fully event-driven (no explicit await), wrap the actual LLM call site.

- [ ] **Step 6.5: Smoke-test the import**

```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent
.venv/bin/python -c "import jarvis_agent; print('ok')"
```

Expected: `ok` with no traceback.

- [ ] **Step 6.6: Run the full voice-agent test suite**

```bash
.venv/bin/python -m pytest tests/ -q 2>&1 | tail -10
```

Expected: 11 new tests from this spec PLUS prior tests pass. Only pre-existing HONCHO failures remain.

- [ ] **Step 6.7: Pre-restart check**

```bash
sqlite3 ~/.local/share/jarvis/turn_telemetry.db "SELECT strftime('%s','now') - strftime('%s', ts_utc) AS age_s FROM turns ORDER BY ts_utc DESC LIMIT 1"
```

If age < 60s, STOP and ask the user before restarting.

- [ ] **Step 6.8: Restart and verify**

```bash
systemctl --user restart jarvis-voice-agent.service
sleep 5
systemctl --user is-active jarvis-voice-agent.service
```

Watch the log briefly:

```bash
tail -f ~/.local/share/jarvis/logs/voice-agent.log
```

Look for `[pre_tts_gate]` lines on the next user turn.

- [ ] **Step 6.9: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/jarvis_agent.py
git commit -m "$(cat <<'EOF'
feat(agent): wire pre-TTS confab gate + front-loaded ack

After generate_reply completes (but before TTS streams), the agent
now calls should_gate(route, text, tool_calls). On trip, it invokes
run_retry_chain with a closure LLM factory that builds any model id
from SPEECH_MODELS. The final reply (retry success or filler) is
voiced; telemetry records the verdict + retry trace.

Front-loaded ack: an 800ms timer fires session.say("One moment.")
when the primary LLM is still pending — perception cushion for
buffered TASK_*/REASONING turns. Cancelled when the primary settles
(whether clean or trip).

Live activation: restart jarvis-voice-agent.service.
Kill switch: JARVIS_PRE_TTS_CONFAB_GATE=0.
EOF
)"
```

---

## Task 7: Live smoke test

**Files:** none modified — verification only.

### Sub-tasks

- [ ] **Step 7.1: Confirm services are up**

```bash
systemctl --user is-active jarvis-voice-agent.service jarvis-voice-client.service
curl -s --max-time 3 http://127.0.0.1:8767/status | python3 -c "import sys,json; d=json.load(sys.stdin); print('agent_present:', d.get('agent_present'))"
```

Both active. agent_present True (wait if needed; host CPU load can delay agent dispatch).

- [ ] **Step 7.2: Trigger a confab-prone request**

Use the chat panel (Task 8 of the previous tray-chat-panel work) OR speak directly:

```bash
curl -s -X POST http://127.0.0.1:8767/user-input \
  -H 'Content-Type: application/json' \
  -d '{"text":"Open Chrome and navigate to YouTube"}'
```

- [ ] **Step 7.3: Watch the log**

```bash
tail -f ~/.local/share/jarvis/logs/voice-agent.log | grep -E "pre_tts_gate|turn-graph:swap"
```

Expected: a `[pre_tts_gate] route=TASK_DESKTOP TRIPPED` line (because Haiku 4.5 typically confabs on this request) followed by retry chain entries. Either:
- `[pre_tts_gate] route=TASK_DESKTOP tier=retry model=claude-sonnet-4-6 PASSED` — Sonnet recovered.
- `[pre_tts_gate] route=TASK_DESKTOP ALL TIERS EXHAUSTED` — filler voiced.

The user hears either a real action result (Sonnet recovery) or the filler "I'm having trouble — could you try again?".

- [ ] **Step 7.4: Verify telemetry**

```bash
sqlite3 ~/.local/share/jarvis/turn_telemetry.db "SELECT ts_utc, route, confab_check_state, confab_pattern_matched, confab_retry_models FROM turns ORDER BY ts_utc DESC LIMIT 3"
```

Expected: the recent turn shows `confab_check_state` matching the gate's verdict (`caught_t1_passed` / `caught_t2_passed` / `caught_filler`), the `confab_pattern_matched` regex source, and the JSON list of models tried.

- [ ] **Step 7.5: Kill-switch verification**

```bash
# Add the kill switch to the systemd unit's env
sudo systemctl --user edit jarvis-voice-agent.service
# Add:
#   [Service]
#   Environment=JARVIS_PRE_TTS_CONFAB_GATE=0
systemctl --user restart jarvis-voice-agent.service
sleep 5
```

Trigger the same confab-prone request. Expected: `[pre_tts_gate] route=TASK_DESKTOP kill_switch` log line; telemetry shows `confab_check_state = 'bypassed_killed'`; the OLD confab behavior returns (you'll hear "Chrome is open" without tools, like before).

Revert the kill switch (`systemctl --user edit ...` and remove the line) when done verifying.

- [ ] **Step 7.6: No commit — Task 7 is verification only.**

---

## Task 8: End-of-feature audit

**Files:** none modified.

### Sub-tasks

- [ ] **Step 8.1: Generate SCOPE/OUT/VERIFY summary**

Print and review:

```
CHANGED:
  - src/voice-agent/pipeline/turn_telemetry.py — schema + state constants
  - src/voice-agent/pipeline/turn_router.py — 8-route label space
  - src/voice-agent/pipeline/specialty_routes.py — NEW dispatch table
  - src/voice-agent/pipeline/pre_tts_confab_gate.py — NEW gate + retry chain
  - src/voice-agent/providers/llm.py — dispatcher consults specialty_routes
  - src/voice-agent/jarvis_agent.py — gate wiring + front-loaded ack
  - src/voice-agent/tests/test_telemetry_migration_pre_tts_gate.py — NEW
  - src/voice-agent/tests/test_specialty_routes_classifier.py — NEW
  - src/voice-agent/tests/test_specialty_routes.py — NEW
  - src/voice-agent/tests/test_dispatcher_specialty_routes.py — NEW
  - src/voice-agent/tests/test_pre_tts_confab_gate.py — NEW
  - src/voice-agent/tests/test_self_improve_wiring.py — adapt to new labels
  - src/voice-agent/tests/test_is_hard_turn_confab.py — adapt to new labels (if needed)
  - docs/superpowers/specs/2026-05-24-pre-tts-confab-gate-design.md — spec (already committed)
  - docs/superpowers/plans/2026-05-24-pre-tts-confab-gate.md — this plan

NOT CHANGED:
  - src/voice-agent/confab_detector.py (looks_like_completion_claim landed in commit 976749de)
  - src/voice-agent/pipeline/skill_review.py (already updated for the new labels in Task 2's downstream-caller update)
  - src/cli/ — CLI agent + bridge untouched
  - src/voice-agent/desktop-tauri/ — desktop UI untouched
  - Voice-agent subagent layer — NO restoration (per spec hygiene rule)
  - The tray indicator — FROZEN

VERIFY (pytest):
  - All new tests pass
  - Full voice-agent suite has only the pre-existing HONCHO failures + test_memory_layer::test_schema_shape (pre-existing)
  - jarvis_agent.py imports cleanly
  - Live smoke shows [pre_tts_gate] log lines on a confab-prone request
  - Telemetry rows show non-NULL confab_check_state values
```

- [ ] **Step 8.2: No commit — audit only.**

---

## Risk callouts (read before starting)

- **Subagent layer must not be restored.** The new module names use "specialty routes" / "route handlers". If any subagent terminology slips into a commit message or code comment, the project rule `.claude/rules/voice-agent.md` is violated.
- **Latency on TASK_*/REASONING turns.** Buffered TTS adds ~1-3s before the user hears anything. Mitigation: front-loaded ack at 800ms. If the user complains the ack is intrusive, the kill switch (`JARVIS_PRE_TTS_CONFAB_GATE=0`) reverts to streaming behavior immediately.
- **DispatchingLLM internals.** Step 4 (and Step 6) assume the dispatcher exposes per-route LLM instances via attributes (`route_to_llm` etc.). Read the actual class definition in `providers/llm.py` before writing the introspection-based tests. If the shape differs, adapt the assertions in `test_dispatcher_specialty_routes.py`.
- **LLM chat API.** The factory closure in Step 6 calls `inner_llm.chat(chat_ctx=..., tools=...)` and iterates chunks. Verify this matches the actual livekit-agents LLM API in this version. If not, adapt.
- **Cost on confab paths.** Worst case: 4 model calls per confab turn (primary + 3 retries). Per-tier 5s timeout means 20s max wall-clock; cost depends on context length. The kill switch and a manual rollback (`git revert`) are the escape valves.
- **Pre-existing failures.** The voice-agent test suite has known-failing tests (HONCHO env leak, `test_schema_shape`) — confirmed in the prior tray-chat-panel work. These are NOT regressions; do not try to fix them as part of this work.
