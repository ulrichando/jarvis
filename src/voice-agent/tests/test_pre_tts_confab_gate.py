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
    async def boom(ctx, specs):
        raise RuntimeError("anthropic 500")
    runner_ok = _FakeRunner(reply_per_call=[
        ("Chrome window now visible.",
         [{"name": "computer_use", "args": {"action": "launch_app"}}]),
    ])

    def factory(model_id: str):
        # First call (tier 1) raises; subsequent calls succeed.
        if model_id == "claude-sonnet-4-6":  # primary AND retry id
            # We want tier 1 to raise, tier 2 to succeed; but with the
            # default ladder for TASK_DESKTOP, tier 1 and tier 2 use
            # different ids (Sonnet then Opus). The factory differentiates.
            # tier 1: claude-sonnet-4-6 with the FIRST factory call
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
