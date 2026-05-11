"""Tests for the validator subagent — pure-function and graceful-
degrade paths. End-to-end LLM calls aren't tested here (network
dependent); we verify the wrapper plumbing is correct."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import tools.validator


def test_is_available_false_without_groq_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert tools.validator.is_available() is False


def test_validate_outcome_returns_unclear_without_key(monkeypatch):
    """No GROQ key → graceful UNCLEAR string, not a crash. Critical
    so the supervisor doesn't treat 'no validator' as 'verified'."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    import asyncio
    fn = tools.validator.validate_outcome._func
    result = asyncio.run(fn(
        user_request="open chrome",
        tool_result="OK: launched 'google-chrome'",
        claimed_outcome="Chrome opened.",
    ))
    assert result.startswith("UNCLEAR:"), result
    assert "validator offline" in result.lower()


def test_validate_outcome_truncates_huge_inputs():
    """Long tool results must not blow up the prompt budget."""
    huge = "x" * 5000
    out = tools.validator._format_for_prompt(huge)
    assert len(out) < len(huge)
    assert "truncated" in out


def test_validator_subagent_registered():
    """The validator subagent must register without crashing even when
    GROQ key is absent (it'll register disabled, that's correct)."""
    from subagents.registry import clear_subagents, SUBAGENT_REGISTRY
    clear_subagents()
    from subagents.validator import register_validator
    register_validator()
    assert "validator" in SUBAGENT_REGISTRY
    spec = SUBAGENT_REGISTRY["validator"]
    assert spec.name == "validator"
    expected_enabled = bool(os.environ.get("GROQ_API_KEY"))
    assert spec.enabled is expected_enabled


def test_validator_subagent_factory_builds():
    """Tool factory must build cleanly — catches import regressions
    same as the browser_v2 task_done bug we hit on 2026-05-01."""
    from subagents.registry import clear_subagents
    clear_subagents()
    from subagents.validator import register_validator, _validator_tools
    register_validator()
    tools = _validator_tools()
    assert isinstance(tools, list) and len(tools) >= 1
    names = [getattr(getattr(t, "_func", t), "__name__", "") for t in tools]
    assert "validate_outcome" in names


def test_validator_uses_groq_8b_for_speed():
    """Validator should default to the cheap fast model. If someone
    'upgrades' it to llama-3.3-70b without thinking, latency
    regresses by ~600ms per validation call."""
    import tools.validator
    src = Path(tools.validator.__file__).read_text()
    # Default model (env-overrideable) should mention 8b.
    assert "llama-3.1-8b" in src, "validator default model is not the cheap 8b"
