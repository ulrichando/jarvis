# Pre-TTS confab gate — pattern coverage + bypass leak Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the visibility + coverage gaps in the existing pre-TTS confab gate so all six confab shapes from today's Instagram session get caught and retried (or filler-voiced) instead of streamed to TTS as plain text.

**Architecture:** Six targeted changes to existing modules; no new files; no architectural shifts.
(1) Add four regex patterns to `confab_detector._STRONG_CLAIMS`.
(2) Add six new state constants to `pipeline/turn_telemetry.py` and keep `CONFAB_STATE_CLEAN` as a legacy alias.
(3) Update `pipeline/pre_tts_confab_gate.telemetry_state_for_clean` to return the precise sub-state per `verdict.reason`, and add INFO-level logging at every verdict-decision point in the gate module.
(4) Update the gate filter in `jarvis_agent.py:3380-3445` so factory-missing and retry-exception paths write distinct states (not `CLEAN`).
(5) Audit `_jarvis_tool_calls_this_turn` reset between turns and fix if a leak is found.
(6) End-to-end regression test that replays the Instagram strings through the gate.

**Tech Stack:** Python 3.13, livekit-agents (already vendored in `.venv`), pytest (needs bootstrap — Task 0), SQLite (telemetry).

**Spec:** `docs/superpowers/specs/2026-05-27-pre-tts-confab-gate-pattern-coverage.md` (committed as `9afeed16`)

---

## File Map

| File | Role | Action |
|---|---|---|
| `src/voice-agent/confab_detector.py` | Pattern source for `looks_like_completion_claim` | Append 4 regexes to `_STRONG_CLAIMS` (Task 2) |
| `src/voice-agent/pipeline/turn_telemetry.py` | Telemetry state constants + log_turn writer | Add 6 new state constants + back-compat alias (Task 1) |
| `src/voice-agent/pipeline/pre_tts_confab_gate.py` | `should_gate`, `run_retry_chain`, `telemetry_state_for_clean` | Update state mapper + add INFO logging at every verdict path (Tasks 3, 4) |
| `src/voice-agent/jarvis_agent.py` | Gate filter `pre_tts_confab_gate_filter`, turn-start handler | Wire new states; verify and (if needed) add `_jarvis_tool_calls_this_turn = []` reset (Tasks 5, 6) |
| `src/voice-agent/tests/test_confab_detector.py` | Existing test file | Add positive + negative cases for the 4 new patterns (Task 2) |
| `src/voice-agent/tests/test_pre_tts_confab_gate.py` | Existing test file | Add cases for precise sub-states, factory-missing, retry-exception, tool_calls leak (Tasks 3, 5, 6) |

---

## Task 0: Bootstrap pytest in the voice-agent venv

**Background:** The `.venv` at `src/voice-agent/.venv/` is missing `pip` and `pytest` (live-verified 2026-05-27). Every subsequent task runs a pytest command, so this MUST land first. Approach: bootstrap pip via `ensurepip`, then `pip install pytest pytest-asyncio`.

**Files:**
- No code changes. Touches `.venv` only (gitignored).

- [ ] **Step 0.1: Confirm the venv really lacks pip and pytest**

Run:
```bash
ls /home/ulrich/Documents/Projects/jarvis/src/voice-agent/.venv/bin/pip 2>&1
/home/ulrich/Documents/Projects/jarvis/src/voice-agent/.venv/bin/python -m pytest --version 2>&1
```
Expected: `No such file or directory` for the first; `No module named pytest` for the second.

- [ ] **Step 0.2: Bootstrap pip via ensurepip**

Run:
```bash
/home/ulrich/Documents/Projects/jarvis/src/voice-agent/.venv/bin/python -m ensurepip --default-pip
```
Expected: `Successfully installed pip-*`.

- [ ] **Step 0.3: Install pytest + pytest-asyncio**

Run:
```bash
/home/ulrich/Documents/Projects/jarvis/src/voice-agent/.venv/bin/python -m pip install pytest pytest-asyncio
```
Expected: `Successfully installed pytest-* pytest-asyncio-*`.

- [ ] **Step 0.4: Verify pytest now works on an existing test**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_confab_detector.py -v --no-header 2>&1 | tail -20
```
Expected: existing tests run (some PASS, possibly some pre-existing FAIL — what matters is pytest collected and ran them, not the pass/fail count).

- [ ] **Step 0.5: No commit (gitignored venv).** Proceed to Task 1.

---

## Task 1: Add precise telemetry state constants

**Files:**
- Modify: `src/voice-agent/pipeline/turn_telemetry.py` (add constants near the existing `CONFAB_STATE_*` block)
- Test: `src/voice-agent/tests/test_turn_telemetry.py` (existing — add one test fn)

- [ ] **Step 1.1: Locate the existing CONFAB_STATE_* constants**

Run:
```bash
grep -n "CONFAB_STATE_" /home/ulrich/Documents/Projects/jarvis/src/voice-agent/pipeline/turn_telemetry.py | head -20
```
Note the line range. Document it as the insertion site (typically the constants are clustered together near the top of the module).

- [ ] **Step 1.2: Write the failing test**

Add to `src/voice-agent/tests/test_turn_telemetry.py`:

```python
def test_new_confab_states_exported():
    """Six new precise sub-states must be importable and distinct from each other and from CLEAN."""
    from pipeline.turn_telemetry import (
        CONFAB_STATE_CLEAN,
        CONFAB_STATE_CLEAN_BYPASS_ROUTE,
        CONFAB_STATE_CLEAN_UNKNOWN_ROUTE,
        CONFAB_STATE_CLEAN_NO_CLAIM,
        CONFAB_STATE_CLEAN_TOOL_CALLED,
        CONFAB_STATE_RETRY_FACTORY_MISSING,
        CONFAB_STATE_RETRY_EXCEPTION,
    )
    new_states = {
        CONFAB_STATE_CLEAN_BYPASS_ROUTE,
        CONFAB_STATE_CLEAN_UNKNOWN_ROUTE,
        CONFAB_STATE_CLEAN_NO_CLAIM,
        CONFAB_STATE_CLEAN_TOOL_CALLED,
        CONFAB_STATE_RETRY_FACTORY_MISSING,
        CONFAB_STATE_RETRY_EXCEPTION,
    }
    assert len(new_states) == 6, "states must be distinct strings"
    assert CONFAB_STATE_CLEAN not in new_states, "legacy CLEAN should remain a separate constant"


def test_legacy_clean_state_unchanged():
    """Existing DB rows use the string 'clean' — back-compat alias must keep that value."""
    from pipeline.turn_telemetry import CONFAB_STATE_CLEAN
    assert CONFAB_STATE_CLEAN == "clean"
```

- [ ] **Step 1.3: Run the failing test**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_turn_telemetry.py::test_new_confab_states_exported tests/test_turn_telemetry.py::test_legacy_clean_state_unchanged -v
```
Expected: FAIL with `ImportError` on the new constants.

- [ ] **Step 1.4: Add the constants**

In `src/voice-agent/pipeline/turn_telemetry.py`, find the `CONFAB_STATE_CLEAN = ...` line. Below the existing `CONFAB_STATE_*` cluster, append:

```python
# Precise sub-states for the gate's "clean" (no-retry) verdicts — added
# 2026-05-27 to make the four bypass reasons distinguishable in the DB
# instead of collapsing them into one indistinguishable CLEAN value.
CONFAB_STATE_CLEAN_BYPASS_ROUTE   = "clean_bypass_route"     # BANTER / EMOTIONAL
CONFAB_STATE_CLEAN_UNKNOWN_ROUTE  = "clean_unknown_route"    # route not TASK_* / REASONING
CONFAB_STATE_CLEAN_NO_CLAIM       = "clean_no_claim"         # text didn't trip any pattern
CONFAB_STATE_CLEAN_TOOL_CALLED    = "clean_tool_called"      # tool_calls non-empty (genuine action)

# New failure-precision states when the gate trips but retry can't run cleanly.
CONFAB_STATE_RETRY_FACTORY_MISSING = "retry_factory_missing"  # gate tripped, _jarvis_pre_tts_llm_factory was None
CONFAB_STATE_RETRY_EXCEPTION       = "retry_exception"        # retry chain raised — see logs
```

Do NOT remove or modify the existing `CONFAB_STATE_CLEAN = "clean"` line — older DB rows still use it.

- [ ] **Step 1.5: Run the test to verify it passes**

Run the same command from Step 1.3.
Expected: both tests PASS.

- [ ] **Step 1.6: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/pipeline/turn_telemetry.py src/voice-agent/tests/test_turn_telemetry.py
git commit -m "feat(voice-agent): add precise confab-state sub-constants for gate telemetry"
```

---

## Task 2: Extend `_STRONG_CLAIMS` with 4 new patterns

**Files:**
- Modify: `src/voice-agent/confab_detector.py` (`_STRONG_CLAIMS` list)
- Test: `src/voice-agent/tests/test_confab_detector.py`

- [ ] **Step 2.1: Locate the existing `_STRONG_CLAIMS` block**

Run:
```bash
grep -n "_STRONG_CLAIMS" /home/ulrich/Documents/Projects/jarvis/src/voice-agent/confab_detector.py | head -5
```
Note the line where the list literal starts.

- [ ] **Step 2.2: Write the failing test**

Add to `src/voice-agent/tests/test_confab_detector.py`:

```python
import pytest

from confab_detector import looks_like_completion_claim


# Today's six confab strings (2026-05-27 Instagram session). Each MUST
# return True from looks_like_completion_claim after Task 2 lands.
CONFAB_STRINGS_2026_05_27 = [
    "On it.",
    "Let me see your screen and navigate to Instagram.",
    "I can see your desktop. Let me focus Chrome and open a new tab to Instagram.",
    "Done — Instagram's loading in a new tab.",
    "It's already open in the tab I just created. Give it a moment to load if it's still spinning.",
    "Done — Instagram's loading.",
]

# Control set — legitimate replies that must NOT match.
LEGIT_CONTROLS = [
    "I'll see what I can do.",
    "I can't see your screen right now.",          # negated — existing negation guard
    "Let me think about that for a moment.",       # "let me" + non-action verb
    "I see what you mean.",                        # no screen-element anchor
    "The forecast is sunny.",
    "I haven't opened that.",                      # negated
    "Let me know if that helps.",                  # "let me" + non-action verb
]


@pytest.mark.parametrize("text", CONFAB_STRINGS_2026_05_27)
def test_confab_2026_05_27_strings_all_detected(text):
    looks, pattern = looks_like_completion_claim(text)
    assert looks is True, (
        f"Expected confab detection for: {text!r}. "
        f"None of _STRONG_CLAIMS matched."
    )


@pytest.mark.parametrize("text", LEGIT_CONTROLS)
def test_legit_controls_not_flagged(text):
    looks, _ = looks_like_completion_claim(text)
    assert looks is False, (
        f"False positive on legit reply: {text!r}. "
        f"A new pattern is too broad — narrow it."
    )
```

- [ ] **Step 2.3: Run the failing tests**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_confab_detector.py -v -k "confab_2026_05_27 or legit_controls"
```
Expected: 4 of 6 confab parametrize cases FAIL (the "On it.", "Let me see…", "I can see…", "It's already open…" cases). The 2 "Done — …" cases should already PASS via the existing Done pattern. All 7 legit controls should PASS.

- [ ] **Step 2.4: Append the 4 new patterns to `_STRONG_CLAIMS`**

Find the `_STRONG_CLAIMS = [` block in `src/voice-agent/confab_detector.py`. Inside the closing `]`, append (preserving existing patterns above):

```python
    # === Added 2026-05-27 — cover confab shapes the original list missed ===

    # Commitment without action ("On it.", "Will do.", "Let me get on it.")
    re.compile(
        r"\b(?:on (?:it|its way)|will do|let me get(?:ting)? on (?:it|that))\b",
        re.IGNORECASE,
    ),

    # Planning narration ("Let me focus Chrome", "Let me click", "Let me see your screen")
    re.compile(
        r"\blet me (?:focus|click|type|open|navigate|go|switch|launch|press|hit|find|search|see)\b",
        re.IGNORECASE,
    ),

    # Hallucinated perception ("I can see your desktop", "I see the screen")
    re.compile(
        r"\bI (?:can |now )?(?:see|am looking at|have on screen)\b.*\b(?:screen|desktop|window|tab|page)\b",
        re.IGNORECASE,
    ),

    # False-state assertion ("It's already open", "The tab's loading")
    re.compile(
        r"\b(?:it'?s|that'?s|the (?:tab|page|window|app)) (?:already )?(?:open|loading|loaded|done|running|launched)\b",
        re.IGNORECASE,
    ),
```

Do not modify any pre-existing pattern. Do not reorder.

- [ ] **Step 2.5: Run the tests to verify they pass**

Run the same command from Step 2.3.
Expected: all 13 parametrize cases PASS (6 confabs + 7 legit).

- [ ] **Step 2.6: Run the full confab_detector test file to catch regressions**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_confab_detector.py -v
```
Expected: every test in the file PASS. If any pre-existing test broke, the new patterns over-fire — diagnose and narrow.

- [ ] **Step 2.7: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/confab_detector.py src/voice-agent/tests/test_confab_detector.py
git commit -m "feat(voice-agent): extend _STRONG_CLAIMS with 4 confab patterns from 2026-05-27 session"
```

---

## Task 3: Update `telemetry_state_for_clean` to return precise sub-states + add INFO logging at every verdict path

**Files:**
- Modify: `src/voice-agent/pipeline/pre_tts_confab_gate.py` (`telemetry_state_for_clean`, `should_gate`, `run_retry_chain`)
- Test: `src/voice-agent/tests/test_pre_tts_confab_gate.py`

- [ ] **Step 3.1: Write the failing tests**

Add to `src/voice-agent/tests/test_pre_tts_confab_gate.py`:

```python
import pytest

from pipeline import pre_tts_confab_gate as gate
from pipeline.turn_telemetry import (
    CONFAB_STATE_CLEAN_BYPASS_ROUTE,
    CONFAB_STATE_CLEAN_UNKNOWN_ROUTE,
    CONFAB_STATE_CLEAN_NO_CLAIM,
    CONFAB_STATE_CLEAN_TOOL_CALLED,
    CONFAB_STATE_BYPASSED_KILLED,
)


@pytest.mark.parametrize("verdict_reason,expected_state", [
    ("bypass_route",    CONFAB_STATE_CLEAN_BYPASS_ROUTE),
    ("unknown_route",   CONFAB_STATE_CLEAN_UNKNOWN_ROUTE),
    ("no_claim",        CONFAB_STATE_CLEAN_NO_CLAIM),
    ("tool_called",     CONFAB_STATE_CLEAN_TOOL_CALLED),
    ("kill_switch",     CONFAB_STATE_BYPASSED_KILLED),
])
def test_telemetry_state_for_clean_precision(verdict_reason, expected_state):
    """telemetry_state_for_clean must map each verdict.reason to a distinct
    state — no more collapsing them all into CONFAB_STATE_CLEAN."""
    v = gate.GateVerdict(should_retry=False, reason=verdict_reason)
    assert gate.telemetry_state_for_clean(v) == expected_state


def test_should_gate_logs_every_decision(caplog):
    """Each false-verdict path must emit one INFO line so we can audit
    why the gate didn't retry. Previously only the trip path logged."""
    caplog.set_level("INFO", logger="jarvis.pre_tts_gate")

    # bypass_route
    gate.should_gate(route="BANTER", text="hi", tool_calls=[])
    # unknown_route
    gate.should_gate(route="WHATEVER", text="hi", tool_calls=[])
    # tool_called
    gate.should_gate(route="TASK_OTHER", text="Done — X.", tool_calls=[{"x": 1}])
    # no_claim
    gate.should_gate(route="TASK_OTHER", text="The forecast is sunny.", tool_calls=[])

    info_records = [r for r in caplog.records if r.levelname == "INFO" and "pre_tts_gate" in r.name]
    # One INFO line per call.
    assert len(info_records) >= 4
    # And each carries its verdict reason in the message.
    reasons_found = {r.message for r in info_records}
    for needle in ("bypass_route", "unknown_route", "tool_called", "no_claim"):
        assert any(needle in m for m in reasons_found), f"missing log line for verdict reason {needle!r}"
```

- [ ] **Step 3.2: Run the failing tests**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_pre_tts_confab_gate.py::test_telemetry_state_for_clean_precision tests/test_pre_tts_confab_gate.py::test_should_gate_logs_every_decision -v
```
Expected: FAIL — the parametrize cases fail because the current `telemetry_state_for_clean` returns CLEAN for everything except kill_switch; the logging test fails because the current `should_gate` only logs on trip.

- [ ] **Step 3.3: Update `telemetry_state_for_clean`**

In `src/voice-agent/pipeline/pre_tts_confab_gate.py`, replace the existing `telemetry_state_for_clean` function with:

```python
def telemetry_state_for_clean(verdict: GateVerdict) -> str:
    """Map a clean verdict (should_retry=False) to its precise telemetry
    sub-state. Each of the four bypass reasons now writes a distinct DB
    value so the operator can tell from the row WHY the gate didn't
    retry — instead of every reason collapsing into CONFAB_STATE_CLEAN.

    The legacy CONFAB_STATE_CLEAN constant remains exported for back-
    compat with older DB rows; new code should land on these sub-states.
    """
    if verdict.reason == "kill_switch":
        return CONFAB_STATE_BYPASSED_KILLED
    if verdict.reason == "bypass_route":
        return CONFAB_STATE_CLEAN_BYPASS_ROUTE
    if verdict.reason == "unknown_route":
        return CONFAB_STATE_CLEAN_UNKNOWN_ROUTE
    if verdict.reason == "tool_called":
        return CONFAB_STATE_CLEAN_TOOL_CALLED
    if verdict.reason == "no_claim":
        return CONFAB_STATE_CLEAN_NO_CLAIM
    # Unknown reason — defensive fallback. Should not happen in
    # practice; if it does, the operator will see "clean" in the DB
    # and know to investigate.
    return CONFAB_STATE_CLEAN
```

Also extend the import block near the top of the file to include the new constants:

```python
from pipeline.turn_telemetry import (
    CONFAB_STATE_CLEAN,
    CONFAB_STATE_CLEAN_BYPASS_ROUTE,
    CONFAB_STATE_CLEAN_UNKNOWN_ROUTE,
    CONFAB_STATE_CLEAN_NO_CLAIM,
    CONFAB_STATE_CLEAN_TOOL_CALLED,
    CONFAB_STATE_CAUGHT_T1_PASSED,
    CONFAB_STATE_CAUGHT_T2_PASSED,
    CONFAB_STATE_CAUGHT_T3_PASSED,
    CONFAB_STATE_CAUGHT_FILLER,
    CONFAB_STATE_BYPASSED_KILLED,
)
```

(Existing imports stay; this just adds the four new clean-sub-state names. Do not remove any existing imports.)

- [ ] **Step 3.4: Add INFO logging at every `should_gate` return path**

In the existing `should_gate` function, add one `logger.info(...)` line just before each `return GateVerdict(...)` statement that returns `should_retry=False`. The trip path (the final `return GateVerdict(True, "confab_detected", ...)`) already has logging downstream — leave its existing behavior alone here; the warning is emitted by the agent filter when the trip path actually fires.

The updated `should_gate` body looks like:

```python
def should_gate(
    *,
    route: str,
    text: str,
    tool_calls: list[Any] | None,
) -> GateVerdict:
    if gate_disabled():
        logger.info(f"[pre_tts_gate] route={route} verdict=kill_switch")
        return GateVerdict(False, "kill_switch")

    if route in _BYPASS_ROUTES:
        logger.info(f"[pre_tts_gate] route={route} verdict=bypass_route")
        return GateVerdict(False, "bypass_route")

    if not route.startswith("TASK_") and route != "REASONING":
        logger.info(f"[pre_tts_gate] route={route} verdict=unknown_route")
        return GateVerdict(False, "unknown_route")

    if tool_calls:
        logger.info(
            f"[pre_tts_gate] route={route} verdict=tool_called "
            f"(n_calls={len(tool_calls)})"
        )
        return GateVerdict(False, "tool_called")

    looks, pattern = looks_like_completion_claim(text)
    if not looks:
        logger.info(f"[pre_tts_gate] route={route} verdict=no_claim")
        return GateVerdict(False, "no_claim")

    # Trip path — agent filter will log a WARNING when it actually
    # runs the retry chain, so we don't double-log here.
    return GateVerdict(True, "confab_detected", pattern_matched=pattern)
```

- [ ] **Step 3.5: Run the tests to verify they pass**

Run the same command from Step 3.2.
Expected: both tests PASS.

- [ ] **Step 3.6: Run the full gate test file to catch regressions**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_pre_tts_confab_gate.py -v
```
Expected: all PASS. If any pre-existing test broke, it likely depended on the old CLEAN-for-everything behavior — fix by updating that test to assert the precise new state.

- [ ] **Step 3.7: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/pipeline/pre_tts_confab_gate.py src/voice-agent/tests/test_pre_tts_confab_gate.py
git commit -m "feat(voice-agent): precise telemetry sub-states + INFO log at every gate verdict"
```

---

## Task 4: Wire factory-missing and retry-exception states in `jarvis_agent.py`

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py` (gate filter at lines 3380-3445; the two graceful-degradation branches)
- Test: `src/voice-agent/tests/test_pre_tts_confab_gate.py`

- [ ] **Step 4.1: Re-read the current gate filter**

Run:
```bash
sed -n '3380,3445p' /home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py
```
Note the two branches that currently set `_jarvis_telemetry_clean(verdict)` on the tripped-but-degraded paths: (a) factory missing (~line 3400-3412), (b) retry chain raised (~line 3432-3444). These both need to write distinct new states.

- [ ] **Step 4.2: Write the failing tests**

Add to `src/voice-agent/tests/test_pre_tts_confab_gate.py`:

```python
@pytest.mark.asyncio
async def test_retry_chain_runs_through_ladder(monkeypatch):
    """Sanity: when the gate trips and a factory is provided, the chain
    walks the ladder and the agent filter would set a CAUGHT_* state.
    This is a positive control — proves the happy path still works
    after Task 3's logging additions."""

    calls = []

    async def fake_runner(model_id):
        async def run(chat_ctx, tool_specs):
            calls.append(model_id)
            # Pretend tier 1 returned a clean reply with a real tool call.
            return ("Opening Chrome.", [{"name": "computer_use", "args": {"action": "focus_app", "app": "Chrome"}}])
        return run

    # Patch the route ladder so we don't depend on env / config.
    from pipeline import specialty_routes
    monkeypatch.setattr(
        specialty_routes,
        "get_route_ladder",
        lambda route: ["claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-7", "gpt-5-mini"],
    )

    result = await gate.run_retry_chain(
        route="TASK_BROWSER",
        chat_ctx=[],
        tool_specs=[],
        original_text="Done — Chrome is open.",
        original_pattern="<pattern>",
        llm_factory=fake_runner,
    )

    assert result.tier_passed == "retry"
    assert result.model_id == "claude-sonnet-4-6"
    assert calls == ["claude-sonnet-4-6"]


# NOTE — the factory-missing and retry-exception states are set inside
# the gate filter in jarvis_agent.py (not in pre_tts_confab_gate.py
# itself), so we cover them with an integration-style test in
# test_jarvis_agent_pre_tts_filter.py if/when that file exists. For
# this PR we settle for the assertion that the constants are wired
# at import time:

def test_new_retry_failure_states_referenced_by_agent():
    """The agent's gate filter must import the two retry-failure
    states so the corresponding code paths can set them. Catches a
    refactor that drops the import."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "jarvis_agent_src",
        "/home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py",
    )
    # Read source directly without executing (full import is heavy).
    src = open(spec.origin).read()
    assert "CONFAB_STATE_RETRY_FACTORY_MISSING" in src, (
        "jarvis_agent.py must reference CONFAB_STATE_RETRY_FACTORY_MISSING "
        "on the factory-missing branch of the gate filter"
    )
    assert "CONFAB_STATE_RETRY_EXCEPTION" in src, (
        "jarvis_agent.py must reference CONFAB_STATE_RETRY_EXCEPTION on "
        "the retry-exception branch of the gate filter"
    )
```

- [ ] **Step 4.3: Run the failing tests**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_pre_tts_confab_gate.py::test_new_retry_failure_states_referenced_by_agent tests/test_pre_tts_confab_gate.py::test_retry_chain_runs_through_ladder -v
```
Expected:
- `test_retry_chain_runs_through_ladder` PASSES already (positive control)
- `test_new_retry_failure_states_referenced_by_agent` FAILS — neither constant is referenced in `jarvis_agent.py` yet

- [ ] **Step 4.4: Update the gate filter imports + factory-missing branch + retry-exception branch in `jarvis_agent.py`**

First, extend the existing import on or around line 273 to include the new states. Find:

```python
from pipeline.pre_tts_confab_gate import (
    should_gate as _pre_tts_should_gate,
    run_retry_chain as _pre_tts_run_retry_chain,
    gate_disabled as _pre_tts_gate_disabled,
    telemetry_state_for_clean as _pre_tts_telemetry_clean,
)
```

Just above it, ensure the telemetry constants are imported. Find the existing `from pipeline.turn_telemetry import` block in the same area and add (if not already present):

```python
from pipeline.turn_telemetry import (
    # … keep existing constants …
    CONFAB_STATE_RETRY_FACTORY_MISSING,
    CONFAB_STATE_RETRY_EXCEPTION,
)
```

(If the existing import already lists `CONFAB_STATE_BYPASSED_KILLED` etc., add the two new names to the same tuple. Don't create a duplicate import block.)

Now update the factory-missing branch. Find (around line 3396-3412):

```python
if llm_factory is None or chat_ctx is None:
    # Factory missing — degrade gracefully: emit the original
    # text but tag the telemetry so we know the gate fired but
    # the retry chain couldn't run.
    logger.warning(
        "[pre_tts_gate] retry chain unavailable (factory or chat_ctx missing) — "
        "emitting original text"
    )
    try:
        sess._jarvis_confab_check_state = _pre_tts_telemetry_clean(verdict)
        sess._jarvis_confab_pattern_matched = verdict.pattern_matched
        sess._jarvis_confab_retry_models = []
    except Exception:
        pass
    if buffer:
        yield buffer
    return
```

Replace the `sess._jarvis_confab_check_state = _pre_tts_telemetry_clean(verdict)` line in that branch with:

```python
        sess._jarvis_confab_check_state = CONFAB_STATE_RETRY_FACTORY_MISSING
```

Leave the surrounding lines untouched.

Then update the retry-exception branch. Find (around line 3432-3444):

```python
except Exception as e:
    # Never let the gate block the user-facing path. On unexpected
    # failure, emit the original text and tag telemetry so the
    # operator can debug from the row.
    logger.exception(f"[pre_tts_gate] retry chain raised: {e}; emitting original text")
    try:
        sess._jarvis_confab_check_state = _pre_tts_telemetry_clean(verdict)
        sess._jarvis_confab_pattern_matched = verdict.pattern_matched
        sess._jarvis_confab_retry_models = []
    except Exception:
        pass
    if buffer:
        yield buffer
```

Replace the `sess._jarvis_confab_check_state = _pre_tts_telemetry_clean(verdict)` line in this branch with:

```python
        sess._jarvis_confab_check_state = CONFAB_STATE_RETRY_EXCEPTION
```

- [ ] **Step 4.5: Run the test to verify it passes**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_pre_tts_confab_gate.py::test_new_retry_failure_states_referenced_by_agent -v
```
Expected: PASS.

- [ ] **Step 4.6: Smoke-test the import (catches typos)**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -c "import jarvis_agent; print('OK')" 2>&1 | tail -3
```
Expected: `OK` printed. If it raises `ImportError: cannot import name 'CONFAB_STATE_RETRY_*'`, the import block was edited wrong — fix and retry.

- [ ] **Step 4.7: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/jarvis_agent.py src/voice-agent/tests/test_pre_tts_confab_gate.py
git commit -m "fix(voice-agent): distinct gate states for factory-missing and retry-exception paths"
```

---

## Task 5: Audit `_jarvis_tool_calls_this_turn` reset (state-leak suspect)

**Files:**
- Read: `src/voice-agent/jarvis_agent.py` (turn-start handler)
- Modify: `src/voice-agent/jarvis_agent.py` IF a leak is confirmed
- Test: `src/voice-agent/tests/test_pre_tts_confab_gate.py`

This task is structured as an audit — the existing code may already be correct. The test below is unconditional; the implementation only changes if the audit shows a leak.

- [ ] **Step 5.1: Locate every reader + writer of `_jarvis_tool_calls_this_turn`**

Run:
```bash
grep -nE "_jarvis_tool_calls_this_turn" /home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py
```
You should see at least: one read in `pre_tts_confab_gate_filter` (line ~3368), one or more writes (set/append/reset) elsewhere. List them.

- [ ] **Step 5.2: Identify the turn-start handler**

Run:
```bash
grep -nE "on_user_turn_completed|on_speech_committed|conversation_item_added|@.*on_turn_start" /home/ulrich/Documents/Projects/jarvis/src/voice-agent/jarvis_agent.py | head -10
```
The handler that fires AT THE START of every user turn (before the LLM call) is the place that must reset `_jarvis_tool_calls_this_turn = []`. Document its line number.

- [ ] **Step 5.3: Write the failing test**

Add to `src/voice-agent/tests/test_pre_tts_confab_gate.py`:

```python
class _FakeSession:
    """Stand-in for a livekit AgentSession with just enough surface
    for the test."""
    def __init__(self):
        self._jarvis_route = "TASK_OTHER"
        self._jarvis_tool_calls_this_turn = []
        self._jarvis_confab_check_state = None
        self._jarvis_confab_pattern_matched = None
        self._jarvis_confab_retry_models = []


def test_should_gate_does_not_see_prior_turn_tool_calls():
    """Regression: if turn N+1 doesn't fire any tool but the session
    attribute still holds turn N's tool_calls list, should_gate would
    bypass with reason 'tool_called' — a state leak that masks confabs
    in the next turn. The fix is whoever sets _jarvis_tool_calls_this_turn
    must reset it to [] at turn start; this test only asserts that
    should_gate's contract handles an empty list correctly. (The reset
    itself is exercised by smoke + live testing.)"""

    sess = _FakeSession()
    sess._jarvis_tool_calls_this_turn = []  # turn-start reset happened
    verdict = gate.should_gate(
        route=sess._jarvis_route,
        text="Done — Instagram's loading.",
        tool_calls=list(sess._jarvis_tool_calls_this_turn),
    )
    assert verdict.should_retry is True, "gate must trip when tool_calls is empty and text claims completion"
    assert verdict.reason == "confab_detected"
```

- [ ] **Step 5.4: Run the test**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_pre_tts_confab_gate.py::test_should_gate_does_not_see_prior_turn_tool_calls -v
```
Expected: PASS already (the contract holds — `should_gate` correctly trips when `tool_calls=[]` and text matches). This proves the gate's contract is fine; the leak (if any) is upstream, in whatever code populates `_jarvis_tool_calls_this_turn`.

- [ ] **Step 5.5: Inspect the turn-start handler — does it reset?**

Open `jarvis_agent.py` at the line you found in Step 5.2. Look for a `session._jarvis_tool_calls_this_turn = []` (or `.clear()`) line inside the handler. If present → audit passes, skip to Step 5.7. If absent → there's a leak to fix.

- [ ] **Step 5.6: (Only if Step 5.5 found a leak) Add the reset**

In the turn-start handler identified in Step 5.2, inside the body (after any guard clauses that might `raise StopResponse` or early-return), add:

```python
        # Reset per-turn tool-call tracker so the pre-TTS gate
        # doesn't see leftover tool_calls from the previous turn
        # and bypass with reason 'tool_called'. The list is appended
        # to by the function_tools_executed handler during this turn.
        session._jarvis_tool_calls_this_turn = []
```

If the existing handler doesn't take `session` as a parameter, use the locally available equivalent (e.g., `self.session` in an `Agent` subclass method).

- [ ] **Step 5.7: Smoke-test the import again**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -c "import jarvis_agent; print('OK')" 2>&1 | tail -3
```
Expected: `OK`.

- [ ] **Step 5.8: Commit**

If Step 5.6 changed code:

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/jarvis_agent.py src/voice-agent/tests/test_pre_tts_confab_gate.py
git commit -m "fix(voice-agent): reset _jarvis_tool_calls_this_turn at turn start to plug gate-bypass leak"
```

If Step 5.5 found no leak (audit passed):

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/tests/test_pre_tts_confab_gate.py
git commit -m "test(voice-agent): regression test for tool_calls-leak gate-bypass scenario"
```

---

## Task 6: End-to-end regression test — replay today's Instagram scenario through the gate

**Files:**
- Test: `src/voice-agent/tests/test_pre_tts_confab_gate.py`

This task adds one final integration-style test that ties everything together: feed each of today's 6 confab strings through `should_gate` with the conditions that held during the live session, and assert the gate would have caught them all.

- [ ] **Step 6.1: Write the test**

Add to `src/voice-agent/tests/test_pre_tts_confab_gate.py`:

```python
# Live evidence from 2026-05-27 — the exact replies that streamed to TTS
# without any tool call firing. After Tasks 2 + 3 + 4, every one of these
# must trip should_gate when called with TASK_OTHER route + empty
# tool_calls.
INSTAGRAM_SESSION_CONFABS_2026_05_27 = [
    "On it.",
    "Let me see your screen and navigate to Instagram.",
    "I can see your desktop. Let me focus Chrome and open a new tab to Instagram.",
    "Done — Instagram's loading in a new tab.",
    "It's already open in the tab I just created. Give it a moment to load if it's still spinning.",
    "Done — Instagram's loading.",
]


@pytest.mark.parametrize("text", INSTAGRAM_SESSION_CONFABS_2026_05_27)
def test_instagram_session_confabs_all_trip_gate(text):
    """Replay 2026-05-27 Instagram session: every confab string above
    streamed to TTS unchallenged because should_gate returned False
    (mostly via pattern miss) or the gate filter never ran (one turn
    showed state=unchecked). After this PR, all six must trip."""
    verdict = gate.should_gate(
        route="TASK_OTHER",
        text=text,
        tool_calls=[],
    )
    assert verdict.should_retry is True, (
        f"Expected gate to trip on confab string: {text!r}. "
        f"Got verdict.reason={verdict.reason!r}, "
        f"pattern_matched={verdict.pattern_matched!r}."
    )
    assert verdict.reason == "confab_detected"
    assert verdict.pattern_matched is not None
```

- [ ] **Step 6.2: Run the test**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/test_pre_tts_confab_gate.py::test_instagram_session_confabs_all_trip_gate -v
```
Expected: all 6 parametrize cases PASS. If any fails, Task 2's pattern additions need adjustment — re-narrow or re-broaden the offending pattern.

- [ ] **Step 6.3: Run the full voice-agent test suite to catch regressions**

Run:
```bash
cd /home/ulrich/Documents/Projects/jarvis/src/voice-agent && .venv/bin/python -m pytest tests/ -x --tb=short -q 2>&1 | tail -25
```
Expected: every test PASS. The `-x` flag stops at the first failure so the report is short and actionable. If a pre-existing test breaks, diagnose: most likely a test that asserted the old `CONFAB_STATE_CLEAN` everywhere — update it to assert the new precise sub-state matching the verdict.

- [ ] **Step 6.4: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/tests/test_pre_tts_confab_gate.py
git commit -m "test(voice-agent): replay 2026-05-27 Instagram confabs against the gate"
```

---

## Task 7: Live verification — restart agent and trigger a confab

**Files:** none changed (verification only).

- [ ] **Step 7.1: Confirm no active session before restart**

Run:
```bash
sqlite3 /home/ulrich/.local/share/jarvis/turn_telemetry.db "SELECT ts_utc FROM turns ORDER BY ts_utc DESC LIMIT 1;"
date -u "+now: %Y-%m-%dT%H:%M:%SZ"
```
If the latest `ts_utc` is within 60 seconds of `now`, STOP and ask the user before restarting. Otherwise proceed.

- [ ] **Step 7.2: Restart voice-agent + voice-client**

Run:
```bash
systemctl --user restart jarvis-voice-agent.service
sleep 5
systemctl --user restart jarvis-voice-client.service
sleep 6
```

- [ ] **Step 7.3: Verify clean startup**

Run:
```bash
journalctl --user -u jarvis-voice-agent.service --since "30 sec ago" -o short-iso | tail -5
grep -E '"level":\s*"ERROR"' /home/ulrich/.local/share/jarvis/logs/voice-agent.log | tail -3
```
Expected: `Started jarvis-voice-agent.service`. No ERROR lines newer than the restart.

- [ ] **Step 7.4: Confirm the gate logger now writes per-turn INFO lines**

Have the user (or yourself if testing manually) say something concrete like "Jarvis, open a new tab and go to instagram". Wait ~10 seconds. Then:

```bash
grep "pre_tts_gate" /home/ulrich/.local/share/jarvis/logs/voice-agent.log | tail -5
```
Expected: at least one `[pre_tts_gate] route=TASK_BROWSER verdict=...` or similar INFO line. If empty, the logger wiring didn't take — re-read step 3.4 and confirm the `logger.info` lines were saved.

- [ ] **Step 7.5: Confirm the latest turn's `confab_check_state` is one of the precise values**

Run:
```bash
sqlite3 /home/ulrich/.local/share/jarvis/turn_telemetry.db "SELECT ts_utc, route, confab_check_state, confab_pattern_matched, confab_retry_models, substr(jarvis_text,1,80) FROM turns ORDER BY ts_utc DESC LIMIT 3;" -separator " | "
```
Expected: `confab_check_state` is one of: `clean_bypass_route`, `clean_unknown_route`, `clean_no_claim`, `clean_tool_called`, `caught_t1_passed`, `caught_t2_passed`, `caught_t3_passed`, `caught_filler`, `retry_factory_missing`, `retry_exception`. NOT the bare `clean` (unless a verdict.reason fell to the defensive fallback — that's a bug; investigate).

- [ ] **Step 7.6: No commit (verification only).** Done.

---

## Self-Review checklist (run after writing this plan, fix inline)

- **Spec coverage:**
  - § Pattern extensions (4 regexes) → Task 2 ✓
  - § Telemetry state precision (6 new constants + alias) → Task 1 + Task 3 ✓
  - § Diagnostic logging at every verdict point → Task 3 ✓
  - § `_jarvis_tool_calls_this_turn` reset audit → Task 5 ✓
  - § Tests (per pattern, per verdict, factory-missing, retry-exception, leak) → Tasks 2/3/4/5/6 ✓
  - § Pytest bootstrap precondition → Task 0 ✓
  - § Live verification path → Task 7 ✓

- **No placeholders.** Every step contains the actual content. No "TBD" or "similar to Task N". ✓

- **Type consistency.** `GateVerdict.reason` strings used in tests match the strings returned by `should_gate` (`"kill_switch"`, `"bypass_route"`, `"unknown_route"`, `"tool_called"`, `"no_claim"`, `"confab_detected"`). The six new constants are referenced by their exact names everywhere. ✓

- **TDD order.** Every code-change task has a failing-test step BEFORE the implementation step. ✓

- **Frequent commits.** Each task ends with one commit. 7 commits total (Task 0 has no commit by design — venv is gitignored). ✓
