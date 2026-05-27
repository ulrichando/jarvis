"""Tests for pipeline.pre_tts_confab_gate."""
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
    verdict = should_gate(
        route="TASK_DESKTOP",
        text="I can't open Chrome — no display attached.",
        tool_calls=[],
    )
    assert verdict.should_retry is False


def test_gate_disabled_via_env():
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


# ── run_retry_chain ──────────────────────────────────────────────

@dataclass
class _FakeRunner:
    reply_per_call: list[tuple[str, list[Any]]]
    calls: list = None

    def __post_init__(self):
        self.calls = []

    async def __call__(self, chat_ctx: Any, tool_specs: list[Any]):
        self.calls.append((chat_ctx, tool_specs))
        if not self.reply_per_call:
            return ("(no more programmed replies)", [])
        return self.reply_per_call.pop(0)


@pytest.mark.asyncio
async def test_retry_chain_tier1_passes():
    runner = _FakeRunner(reply_per_call=[
        ("I've opened Chrome and you can see it.",
         [{"name": "computer_use", "args": {"action": "launch_app"}}]),
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
    assert "I've opened Chrome" in result.text
    assert result.telemetry_state == CONFAB_STATE_CAUGHT_T1_PASSED


@pytest.mark.asyncio
async def test_retry_chain_tier1_fails_tier2_passes():
    runner = _FakeRunner(reply_per_call=[
        ("Chrome is open now.", []),          # tier 1 still confab
        ("Chrome window now visible.",        # tier 2 with tool
         [{"name": "computer_use", "args": {"action": "launch_app"}}]),
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
    assert result.tier_passed == "escalate"
    assert result.telemetry_state == CONFAB_STATE_CAUGHT_T2_PASSED


@pytest.mark.asyncio
async def test_retry_chain_all_tiers_fail_filler_voiced():
    runner = _FakeRunner(reply_per_call=[
        ("Chrome is open.", []),   # tier 1
        ("Done — opened it.", []), # tier 2
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
async def test_retry_chain_handles_runner_exception():
    """If a tier's runner raises, gate logs + continues to next tier."""
    from pipeline.specialty_routes import get_route_ladder
    ladder = get_route_ladder("TASK_DESKTOP")
    tier1_model = ladder[1]  # retry slot — read dynamically, not hardcoded

    async def boom(ctx, specs):
        raise RuntimeError("anthropic 500")
    runner_ok = _FakeRunner(reply_per_call=[
        ("Chrome window now visible.",
         [{"name": "computer_use", "args": {"action": "launch_app"}}]),
    ])

    def factory(model_id: str):
        # tier 1 raises; subsequent tiers (different model ids) succeed.
        if model_id == tier1_model:
            return boom
        return runner_ok

    result = await run_retry_chain(
        route="TASK_DESKTOP",
        chat_ctx=[{"role": "user", "content": "open chrome"}],
        tool_specs=[],
        original_text="Chrome is open.",
        original_pattern=r"chrome",
        llm_factory=factory,
    )
    # Tier 1 raised, tier 2 (Opus) returned clean — escalate succeeds.
    assert result.tier_passed == "escalate"


@pytest.mark.asyncio
async def test_retry_chain_appends_tool_force_prompt():
    runner = _FakeRunner(reply_per_call=[
        ("Chrome is open.", []),
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
    # Inspect the first retry call's chat_ctx — should contain the tool-force prompt.
    first_ctx, _ = runner.calls[0]
    joined = str(first_ctx)
    assert "Your previous response claimed to have completed an action" in joined


# ── _append_system_message unknown-shape guard ──────────────────────

def test_append_system_message_raises_on_unknown_shape():
    from pipeline.pre_tts_confab_gate import _append_system_message
    with pytest.raises(TypeError, match="unsupported chat_ctx type"):
        _append_system_message(object(), "ignored")


# ── telemetry_state_for_clean ───────────────────────────────────────

def test_telemetry_state_for_clean_kill_switch():
    from pipeline.pre_tts_confab_gate import (
        GateVerdict, telemetry_state_for_clean,
    )
    from pipeline.turn_telemetry import CONFAB_STATE_BYPASSED_KILLED
    v = GateVerdict(should_retry=False, reason="kill_switch")
    assert telemetry_state_for_clean(v) == CONFAB_STATE_BYPASSED_KILLED


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


@pytest.mark.asyncio
async def test_retry_chain_runs_through_ladder(monkeypatch):
    """Sanity: when the gate trips and a factory is provided, the chain
    walks the ladder and the agent filter would set a CAUGHT_* state."""

    calls = []

    def fake_runner(model_id):
        async def run(chat_ctx, tool_specs):
            calls.append(model_id)
            return ("Opening Chrome.", [{"name": "computer_use", "args": {"action": "focus_app", "app": "Chrome"}}])
        return run

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


def test_new_retry_failure_states_referenced_by_agent():
    """The agent's gate filter must reference the two retry-failure
    states. Catches a refactor that drops the wiring."""
    from pathlib import Path
    agent_path = Path(__file__).resolve().parent.parent / "jarvis_agent.py"
    src = agent_path.read_text()
    assert "CONFAB_STATE_RETRY_FACTORY_MISSING" in src, (
        "jarvis_agent.py must reference CONFAB_STATE_RETRY_FACTORY_MISSING "
        "on the factory-missing branch of the gate filter"
    )
    assert "CONFAB_STATE_RETRY_EXCEPTION" in src, (
        "jarvis_agent.py must reference CONFAB_STATE_RETRY_EXCEPTION on "
        "the retry-exception branch of the gate filter"
    )


# ── _jarvis_tool_calls_this_turn turn-start reset audit ──────────────

class _FakeSession:
    """Stand-in for a livekit AgentSession with just enough surface for the test."""
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
    in the next turn."""

    sess = _FakeSession()
    sess._jarvis_tool_calls_this_turn = []  # turn-start reset happened
    verdict = gate.should_gate(
        route=sess._jarvis_route,
        text="Done — Instagram's loading.",
        tool_calls=list(sess._jarvis_tool_calls_this_turn),
    )
    assert verdict.should_retry is True, "gate must trip when tool_calls is empty and text claims completion"
    assert verdict.reason == "confab_detected"


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
