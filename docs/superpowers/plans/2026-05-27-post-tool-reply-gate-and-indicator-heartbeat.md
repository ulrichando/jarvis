# Post-tool reply-required gate + indicator heartbeat — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate "JARVIS appears dead during long turns" — supervisor emits tools but no text reply, and the indicator goes green during tool work.

**Architecture:** Two correlated fixes in one PR. Part A: extend `pipeline/pre_tts_confab_gate.py` with a TEXT_FORCE_PROMPT retry path and add a `conversation_item_added` hook in `jarvis_agent.py` that detects silent end-of-turn (assistant item with no text AND no tool_use) and runs the retry chain. Part B: replace `agent_state_changed`-driven thinking-file management with a single 3-second heartbeat task that runs from turn-start to final-reply.

**Tech Stack:** Python 3.13, livekit-agents 1.2.x, pytest + pytest-asyncio, SQLite (telemetry).

**Spec:** `docs/superpowers/specs/2026-05-27-post-tool-reply-gate-and-indicator-heartbeat.md`

**Design decisions made during planning (v1 conservative choices):**
- The spec authorized two implementations of the `no_text_after_tool` detector: (a) inside the streaming gate filter, OR (b) at `conversation_item_added` only. **Chose (b) for v1** — the streaming filter can't reliably distinguish "legitimate tool-use-only iteration" from "silent end-of-turn" without per-iteration tool-call delta tracking that livekit-agents doesn't expose. Belt-and-suspenders at the item level can inspect the full content block list and tell them apart.
- This means `should_gate()` in `pipeline/pre_tts_confab_gate.py` does NOT get a new branch; only `run_retry_chain` (parameterized by reason) and the new constants are added there. The detection lives in `jarvis_agent.py` next to the other `@session.on(...)` handlers.

---

## File structure

| File | Responsibility | New/Modified |
|---|---|---|
| `src/voice-agent/pipeline/turn_telemetry.py` | Add 4 new state constants for the no-text retry path | Modified |
| `src/voice-agent/pipeline/pre_tts_confab_gate.py` | Add `TEXT_FORCE_PROMPT`, `NO_TEXT_FILLER_TEXT`; parameterize `run_retry_chain` to pick the system message based on a new `reason_for_retry` arg | Modified |
| `src/voice-agent/jarvis_agent.py` | Add `_thinking_heartbeat` coroutine + helpers; add `conversation_item_added` belt-and-suspenders handler; add `_post_turn_text_recovery` function; remove thinking-file calls from `_on_agent_state`, `_on_user_input`, `_on_function_tools_executed` | Modified |
| `src/voice-agent/tests/test_pre_tts_confab_gate.py` | Add tests for prompt-by-reason selection + NO_TEXT_FILLER_TEXT path | Modified |
| `src/voice-agent/tests/test_turn_telemetry.py` | Add test that the 4 new state constants exist and are distinct | Modified |
| `src/voice-agent/tests/test_thinking_heartbeat.py` | New file — tests for the heartbeat task lifecycle | Created |
| `src/voice-agent/tests/test_text_recovery_hook.py` | New file — tests for the belt-and-suspenders detection logic at the item-content level | Created |

---

## Task 1: Add 4 telemetry state constants for the no-text retry path

**Files:**
- Modify: `src/voice-agent/pipeline/turn_telemetry.py:38-58`
- Test: `src/voice-agent/tests/test_turn_telemetry.py`

- [ ] **Step 1: Write the failing test**

Append at end of `src/voice-agent/tests/test_turn_telemetry.py`:

```python
def test_no_text_states_distinct_and_exported():
    """The 4 new no_text_* constants must exist and not collide with
    existing CONFAB_STATE_* values."""
    from pipeline.turn_telemetry import (
        CONFAB_STATE_NO_TEXT_T1_PASSED,
        CONFAB_STATE_NO_TEXT_T2_PASSED,
        CONFAB_STATE_NO_TEXT_T3_PASSED,
        CONFAB_STATE_NO_TEXT_FILLER,
        CONFAB_STATE_CLEAN,
        CONFAB_STATE_CAUGHT_T1_PASSED,
        CONFAB_STATE_CAUGHT_FILLER,
    )
    new_states = {
        CONFAB_STATE_NO_TEXT_T1_PASSED,
        CONFAB_STATE_NO_TEXT_T2_PASSED,
        CONFAB_STATE_NO_TEXT_T3_PASSED,
        CONFAB_STATE_NO_TEXT_FILLER,
    }
    existing_states = {
        CONFAB_STATE_CLEAN,
        CONFAB_STATE_CAUGHT_T1_PASSED,
        CONFAB_STATE_CAUGHT_FILLER,
    }
    # All four must be distinct from each other.
    assert len(new_states) == 4, "no_text constants must be unique"
    # And distinct from existing states.
    assert new_states.isdisjoint(existing_states), (
        "no_text constants must not collide with existing CONFAB_STATE_*"
    )
    # Sanity on the actual string values — they need to be DB-stable.
    assert CONFAB_STATE_NO_TEXT_T1_PASSED == "no_text_t1_passed"
    assert CONFAB_STATE_NO_TEXT_T2_PASSED == "no_text_t2_passed"
    assert CONFAB_STATE_NO_TEXT_T3_PASSED == "no_text_t3_passed"
    assert CONFAB_STATE_NO_TEXT_FILLER == "no_text_filler"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_turn_telemetry.py::test_no_text_states_distinct_and_exported -v`

Expected: FAIL with `ImportError: cannot import name 'CONFAB_STATE_NO_TEXT_T1_PASSED'`

- [ ] **Step 3: Add the constants**

In `src/voice-agent/pipeline/turn_telemetry.py`, after line 58 (after the existing `CONFAB_STATE_RETRY_EXCEPTION`), append:

```python
# Post-tool reply-required gate states (2026-05-27). Stored in
# turns.confab_check_state. These mirror the confab cascade but for
# the inverse failure: tool fired but no text reply was voiced.
#   _T1_PASSED: tier 1 (retry) produced text
#   _T2_PASSED: tier 2 (escalate) produced text
#   _T3_PASSED: tier 3 (cross_provider) produced text
#   _FILLER:    all tiers exhausted — safe filler voiced
CONFAB_STATE_NO_TEXT_T1_PASSED  = "no_text_t1_passed"
CONFAB_STATE_NO_TEXT_T2_PASSED  = "no_text_t2_passed"
CONFAB_STATE_NO_TEXT_T3_PASSED  = "no_text_t3_passed"
CONFAB_STATE_NO_TEXT_FILLER     = "no_text_filler"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_turn_telemetry.py::test_no_text_states_distinct_and_exported -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/pipeline/turn_telemetry.py src/voice-agent/tests/test_turn_telemetry.py
git commit -m "telemetry: add 4 CONFAB_STATE_NO_TEXT_* states for text-recovery"
```

---

## Task 2: Add TEXT_FORCE_PROMPT and NO_TEXT_FILLER_TEXT constants

**Files:**
- Modify: `src/voice-agent/pipeline/pre_tts_confab_gate.py:52-62`
- Test: `src/voice-agent/tests/test_pre_tts_confab_gate.py`

- [ ] **Step 1: Write the failing test**

Append at end of `src/voice-agent/tests/test_pre_tts_confab_gate.py`:

```python
def test_text_force_prompt_constant_exists_and_targets_no_text_failure():
    """TEXT_FORCE_PROMPT must instruct the LLM to voice a result and
    NOT call more tools — the inverse of TOOL_FORCE_PROMPT."""
    from pipeline.pre_tts_confab_gate import TEXT_FORCE_PROMPT
    body = TEXT_FORCE_PROMPT.lower()
    assert "did not voice" in body or "did not voice a result" in body
    assert "do not call more tools" in body or "do not call" in body
    # Length sanity — must be a real prompt, not a placeholder.
    assert len(TEXT_FORCE_PROMPT) > 100


def test_no_text_filler_constant_distinct_from_filler_text():
    """The no-text recovery has its own filler distinct from FILLER_TEXT
    so DB telemetry can tell the two failure modes apart."""
    from pipeline.pre_tts_confab_gate import (
        NO_TEXT_FILLER_TEXT, FILLER_TEXT,
    )
    assert NO_TEXT_FILLER_TEXT != FILLER_TEXT
    assert "summary" in NO_TEXT_FILLER_TEXT.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_pre_tts_confab_gate.py::test_text_force_prompt_constant_exists_and_targets_no_text_failure tests/test_pre_tts_confab_gate.py::test_no_text_filler_constant_distinct_from_filler_text -v`

Expected: FAIL with `ImportError: cannot import name 'TEXT_FORCE_PROMPT'` then `NO_TEXT_FILLER_TEXT`.

- [ ] **Step 3: Add the constants**

In `src/voice-agent/pipeline/pre_tts_confab_gate.py`, immediately after line 62 (closing the existing `TOOL_FORCE_PROMPT`), append:

```python
# Text-forcing system message appended for the NO_TEXT_AFTER_TOOL
# retry path. Inverse failure mode of TOOL_FORCE_PROMPT: the LLM
# called tools but emitted no text reply for voice playback.
TEXT_FORCE_PROMPT = (
    "Your previous response called tools but did NOT voice a result. "
    "The user is waiting — they only heard your acknowledgment. "
    "Summarize what you found in 2-3 sentences for voice playback. "
    "Do NOT call more tools. Just give the user the answer in plain text."
)

# Safe filler voiced when the no-text retry chain exhausts. Distinct
# from FILLER_TEXT so operators can tell from telemetry which failure
# mode the row reflects.
NO_TEXT_FILLER_TEXT = (
    "I checked but couldn't put together a clear summary. "
    "Want me to try again?"
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_pre_tts_confab_gate.py::test_text_force_prompt_constant_exists_and_targets_no_text_failure tests/test_pre_tts_confab_gate.py::test_no_text_filler_constant_distinct_from_filler_text -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/pipeline/pre_tts_confab_gate.py src/voice-agent/tests/test_pre_tts_confab_gate.py
git commit -m "gate: add TEXT_FORCE_PROMPT + NO_TEXT_FILLER_TEXT constants"
```

---

## Task 3: Parameterize run_retry_chain to choose prompt + filler + telemetry by reason

**Files:**
- Modify: `src/voice-agent/pipeline/pre_tts_confab_gate.py:168-250`
- Test: `src/voice-agent/tests/test_pre_tts_confab_gate.py`

- [ ] **Step 1: Write the failing tests**

Append at end of `src/voice-agent/tests/test_pre_tts_confab_gate.py`:

```python
@pytest.mark.asyncio
async def test_retry_chain_no_text_reason_uses_text_force_prompt():
    """When reason_for_retry='no_text_after_tool', the appended system
    message must be TEXT_FORCE_PROMPT, not TOOL_FORCE_PROMPT."""
    from pipeline.pre_tts_confab_gate import TEXT_FORCE_PROMPT
    runner = _FakeRunner(reply_per_call=[
        ("Here are the three files that changed: A, B, C.", []),
    ])

    def factory(_model_id):
        return runner

    await run_retry_chain(
        route="TASK_OTHER",
        chat_ctx=[{"role": "user", "content": "review my changes"}],
        tool_specs=[],
        original_text="",
        original_pattern=None,
        llm_factory=factory,
        reason_for_retry="no_text_after_tool",
    )
    first_ctx, _ = runner.calls[0]
    joined = str(first_ctx)
    assert TEXT_FORCE_PROMPT[:60] in joined, (
        "TEXT_FORCE_PROMPT prefix should be present in the retry chat_ctx"
    )
    # And the OLD tool-forcing prompt should NOT have been used.
    assert "Your previous response claimed to have completed" not in joined


@pytest.mark.asyncio
async def test_retry_chain_no_text_tier1_passes_writes_no_text_state():
    """no_text path tier-1 success writes CONFAB_STATE_NO_TEXT_T1_PASSED,
    not CONFAB_STATE_CAUGHT_T1_PASSED."""
    from pipeline.turn_telemetry import CONFAB_STATE_NO_TEXT_T1_PASSED
    runner = _FakeRunner(reply_per_call=[
        ("Here is what I found: the changes look fine.", []),
    ])
    def factory(_m):
        return runner
    result = await run_retry_chain(
        route="TASK_OTHER",
        chat_ctx=[{"role": "user", "content": "review my changes"}],
        tool_specs=[],
        original_text="",
        original_pattern=None,
        llm_factory=factory,
        reason_for_retry="no_text_after_tool",
    )
    assert result.tier_passed == "retry"
    assert result.telemetry_state == CONFAB_STATE_NO_TEXT_T1_PASSED


@pytest.mark.asyncio
async def test_retry_chain_no_text_all_tiers_empty_voices_no_text_filler():
    """All tiers return empty → NO_TEXT_FILLER_TEXT voiced + state is
    CONFAB_STATE_NO_TEXT_FILLER (not CONFAB_STATE_CAUGHT_FILLER)."""
    from pipeline.pre_tts_confab_gate import NO_TEXT_FILLER_TEXT
    from pipeline.turn_telemetry import CONFAB_STATE_NO_TEXT_FILLER
    # Three empty replies in a row — every tier "passes" verdict-wise
    # because text="" + tool_calls=[] doesn't trip confab_detected, so
    # the chain would normally short-circuit at tier 1 with the empty
    # text. The no-text chain must instead detect "still empty" and
    # escalate, ending with the no-text filler if all tiers give up.
    runner = _FakeRunner(reply_per_call=[
        ("", []),  # tier 1
        ("", []),  # tier 2
        ("", []),  # tier 3
    ])
    def factory(_m):
        return runner
    result = await run_retry_chain(
        route="TASK_OTHER",
        chat_ctx=[{"role": "user", "content": "review my changes"}],
        tool_specs=[],
        original_text="",
        original_pattern=None,
        llm_factory=factory,
        reason_for_retry="no_text_after_tool",
    )
    assert result.tier_passed is None
    assert result.text == NO_TEXT_FILLER_TEXT
    assert result.telemetry_state == CONFAB_STATE_NO_TEXT_FILLER
    assert result.model_id == "filler"


@pytest.mark.asyncio
async def test_retry_chain_confab_reason_unchanged_uses_tool_force_prompt():
    """Backward-compat: when reason_for_retry='confab_detected' (default),
    behaviour is unchanged — TOOL_FORCE_PROMPT used, CAUGHT_T1_PASSED state."""
    runner = _FakeRunner(reply_per_call=[
        ("I've opened Chrome and you can see it.",
         [{"name": "computer_use", "args": {}}]),
    ])
    def factory(_m):
        return runner
    result = await run_retry_chain(
        route="TASK_DESKTOP",
        chat_ctx=[{"role": "user", "content": "open chrome"}],
        tool_specs=[],
        original_text="Chrome is open.",
        original_pattern=r"chrome",
        llm_factory=factory,
        reason_for_retry="confab_detected",
    )
    first_ctx, _ = runner.calls[0]
    joined = str(first_ctx)
    assert "Your previous response claimed to have completed" in joined
    assert result.telemetry_state == CONFAB_STATE_CAUGHT_T1_PASSED
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_pre_tts_confab_gate.py -v -k "no_text or confab_reason_unchanged"`

Expected: FAIL — `run_retry_chain()` doesn't accept `reason_for_retry`; or, the no_text branch isn't selecting TEXT_FORCE_PROMPT.

- [ ] **Step 3: Modify `run_retry_chain` to take a `reason_for_retry` arg and branch on it**

Replace `run_retry_chain` in `src/voice-agent/pipeline/pre_tts_confab_gate.py` (currently at lines 168–250). Note: the new branching uses different prompt, different telemetry states, different filler, and a different acceptance rule (no-text path requires `retry_text.strip()` non-empty to consider tier-pass):

```python
async def run_retry_chain(
    *,
    route: str,
    chat_ctx: Any,
    tool_specs: list[Any],
    original_text: str,
    original_pattern: Optional[str],
    llm_factory: LLMFactory,
    reason_for_retry: str = "confab_detected",
) -> RetryResult:
    """Walk the route's ladder. Append the appropriate force-prompt to
    chat_ctx on each retry. Returns the first clean reply, or the
    filler when all tiers exhaust.

    `reason_for_retry` selects branch behavior:
      - 'confab_detected' (default): TOOL_FORCE_PROMPT;
        tier-pass when next call doesn't trip `should_gate`.
        Telemetry: CONFAB_STATE_CAUGHT_T{1,2,3}_PASSED / _FILLER.
      - 'no_text_after_tool': TEXT_FORCE_PROMPT;
        tier-pass when next call returns NON-EMPTY text.
        Telemetry: CONFAB_STATE_NO_TEXT_T{1,2,3}_PASSED / _FILLER.

    Tier indexing: ladder[0] is the primary (the call that already
    confabbed / went silent — skipped here). We start from tier 1.
    """
    ladder = specialty_routes.get_route_ladder(route)
    tier_names = ("primary", "retry", "escalate", "cross_provider")

    if reason_for_retry == "no_text_after_tool":
        force_prompt = TEXT_FORCE_PROMPT
        telemetry_states = (
            None,
            CONFAB_STATE_NO_TEXT_T1_PASSED,
            CONFAB_STATE_NO_TEXT_T2_PASSED,
            CONFAB_STATE_NO_TEXT_T3_PASSED,
        )
        filler_text = NO_TEXT_FILLER_TEXT
        filler_state = CONFAB_STATE_NO_TEXT_FILLER
    else:
        # confab_detected (default) — existing behaviour.
        force_prompt = TOOL_FORCE_PROMPT
        telemetry_states = (
            None,
            CONFAB_STATE_CAUGHT_T1_PASSED,
            CONFAB_STATE_CAUGHT_T2_PASSED,
            CONFAB_STATE_CAUGHT_T3_PASSED,
        )
        filler_text = FILLER_TEXT
        filler_state = CONFAB_STATE_CAUGHT_FILLER

    models_tried: list[str] = [ladder[0]] if ladder[0] else []
    last_text = original_text
    last_pattern = original_pattern

    for tier_idx in range(1, 4):
        model_id = ladder[tier_idx]
        if not model_id:
            continue

        models_tried.append(model_id)
        retry_ctx = _append_system_message(chat_ctx, force_prompt)

        try:
            runner = llm_factory(model_id)
            retry_text, retry_tool_calls = await runner(retry_ctx, tool_specs)
        except Exception as e:
            logger.warning(
                f"[pre_tts_gate] tier={tier_names[tier_idx]} model={model_id} "
                f"reason={reason_for_retry} raised: {type(e).__name__}: {e}"
            )
            continue

        if reason_for_retry == "no_text_after_tool":
            # Tier passes when the retry produced non-empty text.
            if retry_text and retry_text.strip():
                logger.info(
                    f"[pre_tts_gate] route={route} tier={tier_names[tier_idx]} "
                    f"model={model_id} reason=no_text_after_tool PASSED "
                    f"(text len={len(retry_text)})"
                )
                return RetryResult(
                    text=retry_text,
                    tier_passed=tier_names[tier_idx],
                    model_id=model_id,
                    models_tried=models_tried,
                    pattern_matched=original_pattern,
                    telemetry_state=telemetry_states[tier_idx],
                )
            last_text = retry_text or ""
            logger.info(
                f"[pre_tts_gate] route={route} tier={tier_names[tier_idx]} "
                f"model={model_id} reason=no_text_after_tool STILL EMPTY — escalating"
            )
            continue

        # confab_detected — re-run the gate on the retry result.
        verdict = should_gate(
            route=route, text=retry_text, tool_calls=retry_tool_calls,
        )
        if not verdict.should_retry:
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
        last_text = retry_text
        last_pattern = verdict.pattern_matched or last_pattern
        logger.info(
            f"[pre_tts_gate] route={route} tier={tier_names[tier_idx]} "
            f"model={model_id} STILL CONFAB ({verdict.reason}) — escalating"
        )

    # All tiers exhausted — voice the appropriate filler.
    logger.warning(
        f"[pre_tts_gate] route={route} ALL TIERS EXHAUSTED "
        f"(reason={reason_for_retry}) — voicing filler. "
        f"models_tried={models_tried}"
    )
    return RetryResult(
        text=filler_text,
        tier_passed=None,
        model_id="filler",
        models_tried=models_tried,
        pattern_matched=last_pattern,
        telemetry_state=filler_state,
    )
```

Also update the imports at the top of `pre_tts_confab_gate.py` (lines 34-45) to add the new telemetry states:

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
    CONFAB_STATE_NO_TEXT_T1_PASSED,
    CONFAB_STATE_NO_TEXT_T2_PASSED,
    CONFAB_STATE_NO_TEXT_T3_PASSED,
    CONFAB_STATE_NO_TEXT_FILLER,
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_pre_tts_confab_gate.py -v`

Expected: ALL pass (existing tests too — `reason_for_retry` defaults to `confab_detected` for backward compat).

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/pipeline/pre_tts_confab_gate.py src/voice-agent/tests/test_pre_tts_confab_gate.py
git commit -m "gate: parameterize run_retry_chain by reason_for_retry"
```

---

## Task 4: Add belt-and-suspenders detection helpers (pure function)

**Files:**
- Create: `src/voice-agent/pipeline/text_recovery_detect.py`
- Test: `src/voice-agent/tests/test_text_recovery_hook.py`

This task isolates the content-block inspection logic into a pure function we can test without instantiating an AgentSession.

- [ ] **Step 1: Write the failing test**

Create new file `src/voice-agent/tests/test_text_recovery_hook.py`:

```python
"""Tests for pipeline.text_recovery_detect — the pure content-block
inspector that decides whether an assistant item triggered the
silent-end-of-turn failure mode."""
from __future__ import annotations

import pytest


def _text_block(s):
    """Mimic a livekit-agents text content block."""
    class _T:
        type = "text"
        text = s
    return _T()


def _tool_use_block():
    class _TU:
        type = "tool_use"
    return _TU()


def test_item_with_text_and_tool_use_is_interstitial():
    """ack-text + tool_use is the FIRST iteration of a tool-chain turn.
    Don't trigger recovery; the followup hasn't run yet."""
    from pipeline.text_recovery_detect import classify_assistant_item
    cls = classify_assistant_item(
        content=[_text_block("Looking into that."), _tool_use_block()],
        had_prior_tool_calls=False,
    )
    assert cls == "interstitial"


def test_item_with_only_tool_use_is_interstitial():
    """Pure tool_use (silent chain step) is also interstitial."""
    from pipeline.text_recovery_detect import classify_assistant_item
    cls = classify_assistant_item(
        content=[_tool_use_block()],
        had_prior_tool_calls=True,
    )
    assert cls == "interstitial"


def test_item_with_only_text_and_no_prior_tools_is_final():
    """Pure text reply, no tools fired this turn — normal BANTER-shaped turn."""
    from pipeline.text_recovery_detect import classify_assistant_item
    cls = classify_assistant_item(
        content=[_text_block("Hi there.")],
        had_prior_tool_calls=False,
    )
    assert cls == "final_reply"


def test_item_with_only_text_after_tools_is_final():
    """Pure text reply AFTER tools fired — this is the happy path: tool
    chain ran, LLM emitted summary text."""
    from pipeline.text_recovery_detect import classify_assistant_item
    cls = classify_assistant_item(
        content=[_text_block("I found three changes.")],
        had_prior_tool_calls=True,
    )
    assert cls == "final_reply"


def test_empty_item_after_tools_is_silent_failure():
    """No text, no tool_use, BUT tools fired earlier this turn → the
    LLM produced an empty reply where it should have summarized.
    This is the failure mode the recovery path is for."""
    from pipeline.text_recovery_detect import classify_assistant_item
    cls = classify_assistant_item(
        content=[],
        had_prior_tool_calls=True,
    )
    assert cls == "silent_failure"


def test_empty_item_with_no_prior_tools_is_benign_skip():
    """Empty item AND no tool calls — degenerate but not a failure of
    'forgot to voice the result' (nothing was being processed). Skip."""
    from pipeline.text_recovery_detect import classify_assistant_item
    cls = classify_assistant_item(
        content=[],
        had_prior_tool_calls=False,
    )
    assert cls == "benign_empty"


def test_whitespace_only_text_after_tools_is_silent_failure():
    """Text block that's just whitespace doesn't count as a real reply."""
    from pipeline.text_recovery_detect import classify_assistant_item
    cls = classify_assistant_item(
        content=[_text_block("   \n  ")],
        had_prior_tool_calls=True,
    )
    assert cls == "silent_failure"


def test_string_content_supported():
    """Some livekit-agents builds pass content as a list of plain strings
    instead of typed blocks. Detector must handle both shapes."""
    from pipeline.text_recovery_detect import classify_assistant_item
    cls = classify_assistant_item(
        content=["I found the answer."],
        had_prior_tool_calls=True,
    )
    assert cls == "final_reply"


def test_dict_content_supported():
    """Some shapes use dict-style {'type': 'tool_use'} or {'type':'text','text':'…'}."""
    from pipeline.text_recovery_detect import classify_assistant_item
    cls = classify_assistant_item(
        content=[
            {"type": "text", "text": "Looking…"},
            {"type": "tool_use", "name": "computer_use"},
        ],
        had_prior_tool_calls=False,
    )
    assert cls == "interstitial"


def test_none_content_treated_as_empty():
    """item.content=None must not crash the classifier."""
    from pipeline.text_recovery_detect import classify_assistant_item
    cls = classify_assistant_item(
        content=None,
        had_prior_tool_calls=True,
    )
    assert cls == "silent_failure"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_text_recovery_hook.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.text_recovery_detect'`

- [ ] **Step 3: Create the detector module**

Create `src/voice-agent/pipeline/text_recovery_detect.py`:

```python
"""Pure content-block inspector for the post-tool reply-required gate.

Lives outside jarvis_agent.py so it can be unit-tested without
instantiating an AgentSession. Returns one of four classifications:

  - "final_reply": item has text, no tool_use → cancel heartbeat,
    no recovery needed
  - "interstitial": item has tool_use (with or without text) → more
    LLM iterations coming, keep heartbeat running
  - "silent_failure": item has no text AND no tool_use AND prior tool
    calls fired this turn → the LLM gave up; trigger text recovery
  - "benign_empty": item has no text AND no tool_use AND no tool calls
    fired this turn → degenerate but not the failure mode we care about
"""
from __future__ import annotations

from typing import Any


def _block_has_text(b: Any) -> bool:
    """True iff `b` represents a text block with non-whitespace content."""
    if b is None:
        return False
    if isinstance(b, str):
        return bool(b.strip())
    if isinstance(b, dict):
        if b.get("type") == "text":
            return bool((b.get("text") or "").strip())
        return False
    # Typed-object shape (livekit-agents): .type == "text", .text = "..."
    btype = getattr(b, "type", None)
    if btype == "text":
        return bool((getattr(b, "text", None) or "").strip())
    return False


def _block_is_tool_use(b: Any) -> bool:
    """True iff `b` represents a tool_use block."""
    if b is None or isinstance(b, str):
        return False
    if isinstance(b, dict):
        return b.get("type") == "tool_use"
    return getattr(b, "type", None) == "tool_use"


def classify_assistant_item(
    *,
    content: Any,
    had_prior_tool_calls: bool,
) -> str:
    """Classify an assistant conversation item. See module docstring
    for the four return values.

    `content` is item.content (livekit-agents) — may be None, a list of
    typed blocks, a list of strings, a list of dicts, or a mixed list.
    `had_prior_tool_calls` reflects session._jarvis_tool_calls_this_turn
    being non-empty at the moment this item lands.
    """
    blocks = content or []
    if not isinstance(blocks, list):
        # Defensive — content is meant to be a list. Treat singletons
        # the same as a one-element list.
        blocks = [blocks]

    has_text = any(_block_has_text(b) for b in blocks)
    has_tool_use = any(_block_is_tool_use(b) for b in blocks)

    if has_tool_use:
        # tool_use present → interstitial, regardless of text presence.
        return "interstitial"
    if has_text:
        return "final_reply"
    # No text, no tool_use.
    if had_prior_tool_calls:
        return "silent_failure"
    return "benign_empty"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_text_recovery_hook.py -v`

Expected: ALL 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/pipeline/text_recovery_detect.py src/voice-agent/tests/test_text_recovery_hook.py
git commit -m "gate: add classify_assistant_item content-block inspector"
```

---

## Task 5: Add `_post_turn_text_recovery` function in jarvis_agent.py

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py` — add new function after the existing `pre_tts_confab_gate_filter`
- Test: `src/voice-agent/tests/test_text_recovery_hook.py` — add async test

- [ ] **Step 1: Write the failing test**

Append at end of `src/voice-agent/tests/test_text_recovery_hook.py`:

```python
import asyncio


class _FakeSession:
    """Minimal AgentSession stand-in for testing _post_turn_text_recovery.

    Exposes the exact attributes the recovery path reads, plus a
    `say()` capture so we can assert on what got voiced."""
    def __init__(self, *, route, factory, chat_ctx, tool_specs=None):
        self._jarvis_route = route
        self._jarvis_pre_tts_llm_factory = factory
        self._jarvis_pre_tts_tool_specs = tool_specs or []
        self.chat_ctx = chat_ctx
        self._jarvis_confab_check_state = None
        self._jarvis_confab_pattern_matched = None
        self._jarvis_confab_retry_models = []
        self._said: list[str] = []

    def say(self, text, **kwargs):
        self._said.append(text)


def _make_factory(replies):
    """Return an llm_factory that produces a runner emitting the
    provided list of (text, tool_calls) tuples in order."""
    state = {"i": 0}
    async def runner(ctx, specs):
        i = state["i"]
        state["i"] += 1
        if i >= len(replies):
            return ("", [])
        return replies[i]
    def factory(_model_id):
        return runner
    return factory


@pytest.mark.asyncio
async def test_post_turn_text_recovery_voices_tier1_text():
    """Successful tier-1 retry → result voiced via session.say() and
    telemetry state set to CONFAB_STATE_NO_TEXT_T1_PASSED."""
    from jarvis_agent import _post_turn_text_recovery
    from pipeline.turn_telemetry import CONFAB_STATE_NO_TEXT_T1_PASSED

    sess = _FakeSession(
        route="TASK_OTHER",
        factory=_make_factory([
            ("I reviewed the diff. Three files changed in src/cli.", []),
        ]),
        chat_ctx=[{"role": "user", "content": "review my changes"}],
    )
    await _post_turn_text_recovery(sess)
    assert len(sess._said) == 1
    assert "Three files changed" in sess._said[0]
    assert sess._jarvis_confab_check_state == CONFAB_STATE_NO_TEXT_T1_PASSED


@pytest.mark.asyncio
async def test_post_turn_text_recovery_voices_filler_when_chain_empty():
    """All tiers empty → NO_TEXT_FILLER_TEXT voiced; telemetry state is
    CONFAB_STATE_NO_TEXT_FILLER."""
    from jarvis_agent import _post_turn_text_recovery
    from pipeline.pre_tts_confab_gate import NO_TEXT_FILLER_TEXT
    from pipeline.turn_telemetry import CONFAB_STATE_NO_TEXT_FILLER

    sess = _FakeSession(
        route="TASK_OTHER",
        factory=_make_factory([("", []), ("", []), ("", [])]),
        chat_ctx=[{"role": "user", "content": "review my changes"}],
    )
    await _post_turn_text_recovery(sess)
    assert sess._said == [NO_TEXT_FILLER_TEXT]
    assert sess._jarvis_confab_check_state == CONFAB_STATE_NO_TEXT_FILLER


@pytest.mark.asyncio
async def test_post_turn_text_recovery_skips_when_factory_missing():
    """If _jarvis_pre_tts_llm_factory is None, recovery must voice
    NO_TEXT_FILLER_TEXT directly (no retry chain possible)."""
    from jarvis_agent import _post_turn_text_recovery
    from pipeline.pre_tts_confab_gate import NO_TEXT_FILLER_TEXT

    sess = _FakeSession(
        route="TASK_OTHER",
        factory=None,
        chat_ctx=[{"role": "user", "content": "review my changes"}],
    )
    sess._jarvis_pre_tts_llm_factory = None  # explicit override
    await _post_turn_text_recovery(sess)
    assert sess._said == [NO_TEXT_FILLER_TEXT]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_text_recovery_hook.py -v -k "post_turn"`

Expected: FAIL with `ImportError: cannot import name '_post_turn_text_recovery' from 'jarvis_agent'`.

- [ ] **Step 3: Add `_post_turn_text_recovery` to jarvis_agent.py**

In `src/voice-agent/jarvis_agent.py`, add a new function immediately after `pre_tts_confab_gate_filter` (which ends around line 3473). Add this code:

```python
async def _post_turn_text_recovery(session) -> None:
    """Belt-and-suspenders recovery: an assistant item landed in chat_ctx
    with no text AND no tool_use, but the turn had fired tool calls
    earlier. The LLM produced no voiced summary. Run the TEXT_FORCE_PROMPT
    retry chain via run_retry_chain(reason_for_retry="no_text_after_tool")
    and voice the result via session.say() — or voice NO_TEXT_FILLER_TEXT
    if the chain exhausts.

    Sets session._jarvis_confab_check_state for end-of-turn telemetry."""
    route = getattr(session, "_jarvis_route", None) or ""
    llm_factory = getattr(session, "_jarvis_pre_tts_llm_factory", None)
    chat_ctx = getattr(session, "chat_ctx", None)
    tool_specs = list(getattr(session, "_jarvis_pre_tts_tool_specs", None) or [])

    if llm_factory is None or chat_ctx is None:
        # Factory missing — voice the filler directly so the user isn't
        # left with total silence.
        from pipeline.pre_tts_confab_gate import NO_TEXT_FILLER_TEXT
        from pipeline.turn_telemetry import CONFAB_STATE_NO_TEXT_FILLER
        logger.warning(
            "[text-recovery] factory or chat_ctx missing — voicing filler directly"
        )
        try:
            session.say(NO_TEXT_FILLER_TEXT, allow_interruptions=True)
        except Exception as _e:
            logger.debug(f"[text-recovery] say failed: {_e}")
        try:
            session._jarvis_confab_check_state = CONFAB_STATE_NO_TEXT_FILLER
            session._jarvis_confab_pattern_matched = None
            session._jarvis_confab_retry_models = []
        except Exception:
            pass
        return

    logger.warning(
        f"[text-recovery] route={route} silent end-of-turn detected; "
        "running text-force retry chain"
    )
    try:
        result = await _pre_tts_run_retry_chain(
            route=route,
            chat_ctx=chat_ctx,
            tool_specs=tool_specs,
            original_text="",
            original_pattern=None,
            llm_factory=llm_factory,
            reason_for_retry="no_text_after_tool",
        )
    except Exception as e:
        logger.exception(
            f"[text-recovery] retry chain raised: {e}; voicing filler"
        )
        from pipeline.pre_tts_confab_gate import NO_TEXT_FILLER_TEXT
        from pipeline.turn_telemetry import CONFAB_STATE_RETRY_EXCEPTION
        try:
            session.say(NO_TEXT_FILLER_TEXT, allow_interruptions=True)
        except Exception:
            pass
        try:
            session._jarvis_confab_check_state = CONFAB_STATE_RETRY_EXCEPTION
        except Exception:
            pass
        return

    # Voice the result (clean text or filler — both end up here).
    if result.text:
        try:
            session.say(result.text, allow_interruptions=True)
        except Exception as _e:
            logger.debug(f"[text-recovery] say failed: {_e}")

    # Stash telemetry. log_turn reads _jarvis_confab_check_state directly.
    try:
        session._jarvis_confab_check_state = result.telemetry_state
        session._jarvis_confab_pattern_matched = result.pattern_matched
        session._jarvis_confab_retry_models = list(result.models_tried)
    except Exception:
        pass

    logger.info(
        f"[text-recovery] route={route} tier={result.tier_passed!r} "
        f"state={result.telemetry_state} model={result.model_id}"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_text_recovery_hook.py -v -k "post_turn"`

Expected: ALL 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/jarvis_agent.py src/voice-agent/tests/test_text_recovery_hook.py
git commit -m "gate: add _post_turn_text_recovery (text-force retry chain)"
```

---

## Task 6: Add per-turn idempotency guard to `_post_turn_text_recovery` + reset at turn-start

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py` — top of `_post_turn_text_recovery`
- Modify: `src/voice-agent/jarvis_agent.py:4879-4902` — `_on_user_input` reset block

This task makes `_post_turn_text_recovery` safe to invoke multiple times in one turn (e.g., if the framework emits two empty assistant items back-to-back, we recover ONCE). The listener wiring happens in Task 10.

- [ ] **Step 1: Write the failing test**

Append at end of `src/voice-agent/tests/test_text_recovery_hook.py`:

```python
@pytest.mark.asyncio
async def test_post_turn_text_recovery_idempotent_within_one_turn():
    """If _jarvis_text_recovery_fired=True already, calling
    _post_turn_text_recovery a second time is a no-op (no extra say)."""
    from jarvis_agent import _post_turn_text_recovery
    sess = _FakeSession(
        route="TASK_OTHER",
        factory=_make_factory([("First recovery.", [])]),
        chat_ctx=[{"role": "user", "content": "review my changes"}],
    )
    # First call lands and voices.
    await _post_turn_text_recovery(sess)
    assert len(sess._said) == 1
    # Simulate the listener calling a second time after another silent
    # item; the function must short-circuit on its own flag.
    await _post_turn_text_recovery(sess)
    assert len(sess._said) == 1  # still one, not two
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_text_recovery_hook.py::test_post_turn_text_recovery_idempotent_within_one_turn -v`

Expected: FAIL — the second call voices a second message because the flag isn't being honored.

- [ ] **Step 3: Add the idempotency guard to `_post_turn_text_recovery`**

In `src/voice-agent/jarvis_agent.py`, at the TOP of `_post_turn_text_recovery` (immediately after the docstring), insert:

```python
    if getattr(session, "_jarvis_text_recovery_fired", False):
        logger.info("[text-recovery] skipped — flag already set this turn")
        return
    try:
        session._jarvis_text_recovery_fired = True
    except Exception:
        pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_text_recovery_hook.py::test_post_turn_text_recovery_idempotent_within_one_turn -v`

Expected: PASS.

- [ ] **Step 5: Add the flag reset to `_on_user_input` (turn-start)**

In `src/voice-agent/jarvis_agent.py`, locate the existing `_on_user_input` handler at lines 4873-4902. Find this block:

```python
            try:
                session._jarvis_tool_calls_this_turn = []
                session._jarvis_confab_check_state = None
                session._jarvis_confab_pattern_matched = None
                session._jarvis_confab_retry_models = []
            except Exception:
                pass
```

Add one line so it reads:

```python
            try:
                session._jarvis_tool_calls_this_turn = []
                session._jarvis_confab_check_state = None
                session._jarvis_confab_pattern_matched = None
                session._jarvis_confab_retry_models = []
                session._jarvis_text_recovery_fired = False
            except Exception:
                pass
```

- [ ] **Step 6: Run all the gate + hook tests**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_pre_tts_confab_gate.py tests/test_text_recovery_hook.py tests/test_turn_telemetry.py -v`

Expected: ALL PASS.

- [ ] **Step 7: Commit**

```bash
git add src/voice-agent/jarvis_agent.py src/voice-agent/tests/test_text_recovery_hook.py
git commit -m "gate: add idempotency guard + turn-start reset for text-recovery"
```

---

## Task 7: Add `_thinking_heartbeat` coroutine

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py` — add new coroutine near `_mark_thinking_start` / `_mark_thinking_end`
- Test: `src/voice-agent/tests/test_thinking_heartbeat.py` (new file)

- [ ] **Step 1: Write the failing test**

Create `src/voice-agent/tests/test_thinking_heartbeat.py`:

```python
"""Tests for the thinking-indicator heartbeat task."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest


def _file_age_s(p: Path) -> float:
    return time.time() - p.stat().st_mtime


@pytest.mark.asyncio
async def test_heartbeat_touches_file_repeatedly(tmp_path, monkeypatch):
    """Heartbeat keeps the file fresh — after 0.6s with 0.2s sleep
    interval, file mtime should be less than the heartbeat interval old."""
    from jarvis_agent import _thinking_heartbeat
    fake_file = tmp_path / ".agent-thinking"
    monkeypatch.setattr("jarvis_agent._AGENT_THINKING_FILE", fake_file)

    task = asyncio.create_task(_thinking_heartbeat(interval_s=0.2))
    try:
        # Let the heartbeat run for 3 ticks.
        await asyncio.sleep(0.65)
        assert fake_file.exists(), "heartbeat should have created the file"
        # The mtime should be within 0.3s (one interval + slack).
        assert _file_age_s(fake_file) < 0.3
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_heartbeat_unlinks_file_on_cancel(tmp_path, monkeypatch):
    from jarvis_agent import _thinking_heartbeat
    fake_file = tmp_path / ".agent-thinking"
    monkeypatch.setattr("jarvis_agent._AGENT_THINKING_FILE", fake_file)

    task = asyncio.create_task(_thinking_heartbeat(interval_s=0.1))
    await asyncio.sleep(0.2)
    assert fake_file.exists()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    # Cancellation must remove the file so the desktop indicator goes green.
    assert not fake_file.exists()


@pytest.mark.asyncio
async def test_heartbeat_survives_repeated_unlinks(tmp_path, monkeypatch):
    """Simulate the desktop racing the agent: file gets unlinked
    externally; heartbeat must re-create it on the next tick so the
    indicator doesn't blink green."""
    from jarvis_agent import _thinking_heartbeat
    fake_file = tmp_path / ".agent-thinking"
    monkeypatch.setattr("jarvis_agent._AGENT_THINKING_FILE", fake_file)

    task = asyncio.create_task(_thinking_heartbeat(interval_s=0.1))
    try:
        await asyncio.sleep(0.15)
        assert fake_file.exists()
        fake_file.unlink()
        await asyncio.sleep(0.2)
        assert fake_file.exists(), "heartbeat should re-touch after external unlink"
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_heartbeat_exits_cleanly_on_cancel_during_sleep(tmp_path, monkeypatch):
    """Cancel during the sleep portion of the loop — heartbeat must
    still unlink and exit (no hang)."""
    from jarvis_agent import _thinking_heartbeat
    fake_file = tmp_path / ".agent-thinking"
    monkeypatch.setattr("jarvis_agent._AGENT_THINKING_FILE", fake_file)

    task = asyncio.create_task(_thinking_heartbeat(interval_s=10.0))  # long sleep
    await asyncio.sleep(0.1)  # let it touch once
    assert fake_file.exists()
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=1.0)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        pass
    assert task.cancelled() or task.done()
    assert not fake_file.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_thinking_heartbeat.py -v`

Expected: FAIL with `ImportError: cannot import name '_thinking_heartbeat' from 'jarvis_agent'`.

- [ ] **Step 3: Add `_thinking_heartbeat` to jarvis_agent.py**

In `src/voice-agent/jarvis_agent.py`, immediately after the existing `_mark_thinking_end` function (around line 958), append:

```python
# Heartbeat-driven thinking-indicator (2026-05-27). Replaces the
# agent_state_changed-driven file management which broke during long
# turns: the framework transitioned through "listening" or "speaking"
# between tool calls, the file got unlinked, indicator went green
# while JARVIS was actively reviewing/researching for the user.
#
# The heartbeat task starts on user_input_transcribed(is_final=True)
# and runs until the assistant emits a FINAL reply (text content, no
# tool_use) or until the turn is interrupted/cancelled. While running,
# it re-touches _AGENT_THINKING_FILE every `interval_s` seconds — the
# desktop's 60s TTL becomes a generous floor instead of the operative
# expiry.
async def _thinking_heartbeat(interval_s: float = 3.0) -> None:
    """Touch _AGENT_THINKING_FILE every `interval_s` seconds.

    On cancellation, unlinks the file so the desktop indicator goes
    green immediately. Idempotent: external unlinks are repaired on
    the next tick."""
    try:
        while True:
            _mark_thinking_start()
            await asyncio.sleep(interval_s)
    except asyncio.CancelledError:
        _mark_thinking_end()
        raise
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_thinking_heartbeat.py -v`

Expected: ALL 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/jarvis_agent.py src/voice-agent/tests/test_thinking_heartbeat.py
git commit -m "indicator: add _thinking_heartbeat task"
```

---

## Task 8: Add heartbeat lifecycle helpers (start/cancel)

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py` — add `_start_thinking_heartbeat(session)` and `_cancel_thinking_heartbeat(session)`
- Test: `src/voice-agent/tests/test_thinking_heartbeat.py`

- [ ] **Step 1: Write the failing test**

Append at end of `src/voice-agent/tests/test_thinking_heartbeat.py`:

```python
class _FakeSessionHB:
    """Stand-in for AgentSession.run-time. Only holds the heartbeat
    task slot the helpers manage."""
    def __init__(self):
        self._jarvis_thinking_heartbeat = None


@pytest.mark.asyncio
async def test_start_helper_creates_task_and_stores_on_session(tmp_path, monkeypatch):
    from jarvis_agent import _start_thinking_heartbeat
    monkeypatch.setattr("jarvis_agent._AGENT_THINKING_FILE", tmp_path / ".agent-thinking")
    sess = _FakeSessionHB()
    _start_thinking_heartbeat(sess, interval_s=0.1)
    try:
        assert sess._jarvis_thinking_heartbeat is not None
        assert not sess._jarvis_thinking_heartbeat.done()
        await asyncio.sleep(0.15)
        assert (tmp_path / ".agent-thinking").exists()
    finally:
        sess._jarvis_thinking_heartbeat.cancel()
        try:
            await sess._jarvis_thinking_heartbeat
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_start_helper_cancels_prior_task_defensively(tmp_path, monkeypatch):
    """Back-to-back calls — only the newest task runs."""
    from jarvis_agent import _start_thinking_heartbeat
    monkeypatch.setattr("jarvis_agent._AGENT_THINKING_FILE", tmp_path / ".agent-thinking")
    sess = _FakeSessionHB()
    _start_thinking_heartbeat(sess, interval_s=0.1)
    first = sess._jarvis_thinking_heartbeat
    _start_thinking_heartbeat(sess, interval_s=0.1)
    second = sess._jarvis_thinking_heartbeat
    # The second call must have cancelled the first and replaced it.
    await asyncio.sleep(0.05)
    assert first is not second
    assert first.cancelled() or first.done()
    assert not second.done()
    # Cleanup.
    second.cancel()
    try:
        await second
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_cancel_helper_handles_missing_task(tmp_path, monkeypatch):
    """If no heartbeat is running, cancel is a no-op."""
    from jarvis_agent import _cancel_thinking_heartbeat
    monkeypatch.setattr("jarvis_agent._AGENT_THINKING_FILE", tmp_path / ".agent-thinking")
    sess = _FakeSessionHB()
    # Should not raise.
    _cancel_thinking_heartbeat(sess)
    assert sess._jarvis_thinking_heartbeat is None


@pytest.mark.asyncio
async def test_cancel_helper_unlinks_file(tmp_path, monkeypatch):
    from jarvis_agent import _start_thinking_heartbeat, _cancel_thinking_heartbeat
    fake_file = tmp_path / ".agent-thinking"
    monkeypatch.setattr("jarvis_agent._AGENT_THINKING_FILE", fake_file)
    sess = _FakeSessionHB()
    _start_thinking_heartbeat(sess, interval_s=0.1)
    await asyncio.sleep(0.15)
    assert fake_file.exists()
    _cancel_thinking_heartbeat(sess)
    # Give the cancellation a moment to drain.
    await asyncio.sleep(0.05)
    assert not fake_file.exists()
    assert sess._jarvis_thinking_heartbeat is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_thinking_heartbeat.py -v`

Expected: FAIL with `ImportError: cannot import name '_start_thinking_heartbeat'`.

- [ ] **Step 3: Add the two helpers**

In `src/voice-agent/jarvis_agent.py`, immediately after `_thinking_heartbeat`, append:

```python
def _start_thinking_heartbeat(session, interval_s: float = 3.0) -> None:
    """Start (or restart) the heartbeat task on this session. Any prior
    task is cancelled defensively — handles back-to-back user inputs
    that arrive faster than the previous turn-end."""
    prior = getattr(session, "_jarvis_thinking_heartbeat", None)
    if prior is not None and not prior.done():
        prior.cancel()
    try:
        session._jarvis_thinking_heartbeat = asyncio.create_task(
            _thinking_heartbeat(interval_s=interval_s)
        )
    except Exception as _e:
        logger.debug(f"[heartbeat] start failed: {_e}")
        session._jarvis_thinking_heartbeat = None


def _cancel_thinking_heartbeat(session) -> None:
    """Cancel the heartbeat task on this session if running. Idempotent."""
    task = getattr(session, "_jarvis_thinking_heartbeat", None)
    if task is None:
        return
    if not task.done():
        task.cancel()
    session._jarvis_thinking_heartbeat = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_thinking_heartbeat.py -v`

Expected: ALL tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/jarvis_agent.py src/voice-agent/tests/test_thinking_heartbeat.py
git commit -m "indicator: add _start/_cancel_thinking_heartbeat helpers"
```

---

## Task 9: Wire heartbeat start into `_on_user_input` (turn-start)

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py:4873-4902`

- [ ] **Step 1: Locate the existing `_on_user_input` handler**

It currently looks like:

```python
    @session.on("user_input_transcribed")
    def _on_user_input(ev) -> None:
        try:
            from resilience import audio_silence_watchdog as _asw
            _asw.mark_audio_activity()
        except Exception:
            pass
        if getattr(ev, "is_final", True):
            _mark_thinking_start()
            _reset_tool_call_count()
            ...
```

- [ ] **Step 2: Replace the body**

In `src/voice-agent/jarvis_agent.py`, replace the `_mark_thinking_start()` call inside `_on_user_input` (line 4880) with the heartbeat start:

```python
        if getattr(ev, "is_final", True):
            # Start the indicator heartbeat. Heartbeat runs from now
            # until the FINAL assistant reply lands (text-only, no
            # tool_use) or until the turn is barge-in / interrupted.
            # Replaces the prior agent_state-driven _mark_thinking_start
            # call — see docs/superpowers/specs/2026-05-27-post-tool-reply-gate-and-indicator-heartbeat.md
            _start_thinking_heartbeat(session)
            _reset_tool_call_count()
```

The rest of the function (the existing `session._jarvis_tool_calls_this_turn = []` block, the dispatch_agent reset, the new `_jarvis_text_recovery_fired = False` line added in Task 6) stays unchanged.

- [ ] **Step 3: Run the agent's existing user_input tests to confirm no regressions**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/ -v -k "user_input or turn_start"`

Expected: existing tests PASS (or skip cleanly if they were import-only). Should not produce any new failures.

- [ ] **Step 4: Commit**

```bash
git add src/voice-agent/jarvis_agent.py
git commit -m "indicator: start heartbeat on user_input_transcribed(final)"
```

---

## Task 10: Wire heartbeat cancel into `_on_item` (final-reply detection)

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py:5635-5690`

- [ ] **Step 1: Update the conversation_item_added handler**

In the existing `_on_item` handler, the block at lines 5689-5690 currently is:

```python
            if role == "assistant":
                _mark_thinking_end()
```

Replace it with the consolidated classifier branch that decides heartbeat lifecycle AND silent-failure recovery in one place:

```python
            if role == "assistant":
                # Use the pure classifier to decide what kind of
                # assistant item this is and how to handle it:
                #   final_reply / benign_empty → cancel heartbeat (turn done)
                #   silent_failure → fire text recovery; recovery's voiced
                #                    output later lands as ANOTHER item that
                #                    classifies as final_reply, cancelling
                #                    the heartbeat then
                #   interstitial   → keep heartbeat running (more LLM iterations
                #                    coming after the tool batch lands)
                try:
                    from pipeline.text_recovery_detect import classify_assistant_item
                    had_tools = bool(
                        getattr(session, "_jarvis_tool_calls_this_turn", None) or []
                    )
                    cls = classify_assistant_item(
                        content=getattr(item, "content", None),
                        had_prior_tool_calls=had_tools,
                    )
                except Exception as _e:
                    logger.debug(f"[heartbeat] classify skipped: {_e}")
                    cls = "final_reply"  # fail open — cancel heartbeat

                if cls in ("final_reply", "benign_empty"):
                    _cancel_thinking_heartbeat(session)
                elif cls == "silent_failure":
                    # DON'T cancel heartbeat yet — recovery produces a
                    # follow-up assistant item; that one classifies as
                    # final_reply and cancels the heartbeat.
                    asyncio.create_task(_post_turn_text_recovery(session))
                # else cls == "interstitial" → keep heartbeat running.
```

This single block replaces the `_mark_thinking_end()` call AND is the only listener wiring for the text-recovery hook (Task 6 only added the idempotency guard and the turn-start reset — listener wiring is HERE).

- [ ] **Step 2: Run the gate tests to confirm Task-6 behaviour is preserved**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_text_recovery_hook.py tests/test_thinking_heartbeat.py -v`

Expected: ALL PASS — the unit-level behaviour is unchanged (helpers are still called from `_post_turn_text_recovery`, and `classify_assistant_item` is still correct).

- [ ] **Step 3: Commit**

```bash
git add src/voice-agent/jarvis_agent.py
git commit -m "indicator: cancel heartbeat on final assistant reply; fold text-recovery branch in"
```

---

## Task 11: Wire heartbeat cancel into the barge-in handler

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py:4965-5039`

- [ ] **Step 1: Locate the barge-in interrupt site**

There are two barge-in paths that call `session.interrupt()`:
1. `_on_user_input_kill_phrase` around line 4959
2. `_on_user_state_for_interrupt` around line 5037

Both need the heartbeat cancel because barge-in IS a turn-end (the LLM iteration is being aborted; no further assistant items will land for this turn).

- [ ] **Step 2: Add cancel after each interrupt call**

In `src/voice-agent/jarvis_agent.py`, find the line `session.interrupt(force=True)` in `_on_user_input_kill_phrase` (line 4959). Add immediately after:

```python
            _cancel_thinking_heartbeat(session)
```

In the same file, find the line `session.interrupt()` in `_on_user_state_for_interrupt` (line 5037). Add immediately after:

```python
            _cancel_thinking_heartbeat(session)
```

(There's also a `session.interrupt(force=True)` around line 4984 in `_on_user_input_echo_aware_interrupt`. Add the cancel there too, after that line.)

- [ ] **Step 3: Run the full voice-agent test suite to confirm no regressions**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/ -v -k "barge or interrupt or kill_phrase or heartbeat"`

Expected: All matched tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/voice-agent/jarvis_agent.py
git commit -m "indicator: cancel heartbeat on barge-in interrupt paths"
```

---

## Task 12: Remove `_mark_thinking_start`/`_mark_thinking_end` from `_on_agent_state`

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py:4757-4866`

With the heartbeat owning the file lifecycle, the agent_state_changed handler's file management is redundant.

- [ ] **Step 1: Remove `_mark_thinking_start()` from the thinking-state branch**

Locate (around line 4761-4762):

```python
        if new_state == "thinking":
            _mark_thinking_start()
```

Change to:

```python
        if new_state == "thinking":
            # Heartbeat owns _AGENT_THINKING_FILE now (started in
            # _on_user_input). Don't touch the file here — the framework's
            # transient "listening" state between tool calls would have
            # otherwise unlinked it and made the tray go green.
```

(Keep the `# Front-loaded ack (2026-05-24, ...)` block that follows — that's independent of the heartbeat.)

- [ ] **Step 2: Remove `_mark_thinking_end()` from the idle/listening branch**

Locate (around line 4820-4827):

```python
        elif new_state in ("idle", "listening"):
            # NOT "speaking" — speaking is INTERSTITIAL: ...
            _mark_thinking_end()
            _mark_tool_end()
```

Change to:

```python
        elif new_state in ("idle", "listening"):
            # Heartbeat owns _AGENT_THINKING_FILE — cancel happens in
            # _on_item (final_reply detection) or in barge-in paths,
            # not here. Keep _mark_tool_end() since the tool-busy file
            # is separate from the thinking flag.
            _mark_tool_end()
```

- [ ] **Step 3: Run the heartbeat tests + the existing agent-state tests**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/test_thinking_heartbeat.py tests/test_text_recovery_hook.py tests/ -v -k "agent_state or thinking"`

Expected: ALL PASS.

- [ ] **Step 4: Commit**

```bash
git add src/voice-agent/jarvis_agent.py
git commit -m "indicator: remove thinking-file calls from _on_agent_state (heartbeat owns it)"
```

---

## Task 13: Remove `_mark_thinking_start` from `_on_user_input` and `_on_function_tools_executed`

**Files:**
- Modify: `src/voice-agent/jarvis_agent.py:4910-4947`

The Task-9 change replaced the `_on_user_input` call with `_start_thinking_heartbeat`. Now do the same cleanup on `_on_function_tools_executed`.

- [ ] **Step 1: Remove the redundant call**

Locate (around lines 4915-4922):

```python
            # Refresh the thinking-flag file on every tool batch. The
            # framework's agent_state→speaking transition unlinks the
            # file when JARVIS voices the ack ("On it.") — but the LLM
            # is still iterating through tool calls + a followup reply,
            # which can take 10-60+ s. Without this refresh, the tray
            # goes green during that work and the user sees "JARVIS is
            # silent" when JARVIS is actively reviewing/researching.
            _mark_thinking_start()
```

Change the call to a comment explaining why it's now redundant:

```python
            # Tool-batch completion is no longer a moment we need to
            # re-touch the thinking-flag file — the heartbeat (started
            # in _on_user_input) refreshes it every 3s for the whole
            # turn. Kept this handler for the dispatch_agent telemetry
            # stash + tool-calls accumulator that follow.
```

- [ ] **Step 2: Run the full test suite to confirm no regressions**

Run: `cd src/voice-agent && .venv/bin/python -m pytest tests/ -v -x 2>&1 | tail -30`

Expected: ALL PASS (or the same flaky/skipped tests as baseline — diff against the green count from start of plan).

- [ ] **Step 3: Commit**

```bash
git add src/voice-agent/jarvis_agent.py
git commit -m "indicator: remove redundant _mark_thinking_start from function_tools_executed"
```

---

## Task 14: Integration verification — manual smoke test against live voice-agent

This task verifies the change end-to-end against the running JARVIS. It is mandatory.

- [ ] **Step 1: Check the telemetry DB before restart**

Run: `sqlite3 ~/.local/share/jarvis/turn_telemetry.db "SELECT ts_utc FROM turns ORDER BY id DESC LIMIT 1"`

If the latest `ts_utc` is within 60s, ASK THE USER before restarting (per the load-bearing rule in `CLAUDE.md`). Otherwise proceed.

- [ ] **Step 2: Restart the voice-agent**

Run: `systemctl --user restart jarvis-voice-agent.service`

Expected: command returns quickly; service starts.

- [ ] **Step 3: Verify the agent imports cleanly**

Run: `tail -50 ~/.local/share/jarvis/logs/voice-agent.log | grep -E "ERROR|Traceback|ImportError"`

Expected: no new errors. If there are any, STOP — fix the import issue before proceeding.

- [ ] **Step 4: Manual voice test — confab-trigger turn**

Say to JARVIS: *"Jarvis, review my uncommitted changes."*

Expected:
- Ack plays ("Looking into that." or similar) within ~800ms
- The tray indicator stays AMBER from ack through final reply (no flicker to green)
- A final summary plays (2-3 sentences about the changes) within ~10-15s

If the indicator flickers green mid-turn: the heartbeat may not be starting. Re-check Task 9.

If the final summary doesn't play: the text-recovery hook may not be firing. Re-check Task 10's silent_failure branch.

- [ ] **Step 5: Inspect telemetry for the test turn**

Run:
```bash
sqlite3 ~/.local/share/jarvis/turn_telemetry.db \
  "SELECT ts_utc, route, llm_used, confab_check_state, jarvis_text FROM turns ORDER BY id DESC LIMIT 3"
```

Expected: the turn row's `confab_check_state` is one of:
- `clean_*` if JARVIS naturally voiced a reply (happy path — no recovery needed)
- `no_text_t1_passed` / `no_text_t2_passed` / `no_text_t3_passed` if the recovery chain triggered and succeeded
- `no_text_filler` if the recovery chain exhausted

If the state is `clean_no_claim` and `jarvis_text` is empty: the silent failure happened AND the recovery hook didn't fire. Debug from logs:

```bash
grep -E "text-recovery|heartbeat" ~/.local/share/jarvis/logs/voice-agent.log | tail -30
```

- [ ] **Step 6: Manual voice test — barge-in path**

Say to JARVIS: *"Jarvis, can you tell me a story about a robot?"*

While JARVIS is speaking, say: *"Stop."*

Expected:
- TTS stops within ~400ms
- Tray indicator goes green (heartbeat cancelled by barge-in)
- The next turn works normally

If the indicator stays amber after barge-in: the barge-in cancel paths from Task 11 didn't fire. Check the kill-phrase / VAD interrupt handler logs.

- [ ] **Step 7: Final commit-or-clean checkpoint**

Run: `git status` and `git log --oneline -15`

Expected: 13 commits from this plan, none from unrelated paths. If there's drift, STOP and surface it to the user.

---

## Verification checklist (Spec coverage)

Map each spec section to its implementing task(s):

- Spec §A "Detection" → NOT implemented in `should_gate` (planning-time decision: belt-and-suspenders only in v1). Justification documented at top of plan.
- Spec §A "Retry behavior" → Task 3 (parameterize run_retry_chain)
- Spec §A "Filler" → Task 2 (NO_TEXT_FILLER_TEXT constant) + Task 3 (filler selection by reason)
- Spec §A "Belt-and-suspenders hook" → Task 4 (classifier) + Task 6 (wire) + Task 10 (consolidate)
- Spec §A `_post_turn_text_recovery` → Task 5
- Spec §A Telemetry (4 new states) → Task 1
- Spec §B "Heartbeat task" → Task 7
- Spec §B "Lifecycle Start" → Task 8 (helpers) + Task 9 (wire start)
- Spec §B "Lifecycle End (normal)" → Task 10
- Spec §B "Lifecycle End (barge-in)" → Task 11
- Spec §B "Simplification of agent_state_changed handler" → Task 12 + Task 13
- Spec §Testing (4 + 1 + 4 = 9 unit tests) → covered by Tasks 1-8 (tests are co-located with each task)
- Spec §Verification path → Task 14
