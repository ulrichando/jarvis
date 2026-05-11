"""Tests for the code-reviewer subagent. Pure-function + graceful-
degrade paths only — end-to-end LLM calls are network-dependent."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import tools.code_reviewer


def test_is_available_false_without_groq_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert tools.code_reviewer.is_available() is False


def test_review_code_returns_offline_message_without_key(monkeypatch):
    """No GROQ key → graceful "(reviewer offline)" string, not a
    crash. Critical so the supervisor doesn't panic when the optional
    dep isn't configured."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    import asyncio
    fn = tools.code_reviewer.review_code._func
    result = asyncio.run(fn(code="def foo(): pass", focus="", context=""))
    assert "offline" in result.lower()


def test_format_for_prompt_truncates_huge_inputs():
    """Code review of huge inputs must trim — the LLM's depth caps
    around 8KB anyway."""
    huge = "x" * 100000
    out = tools.code_reviewer._format_for_prompt(huge)
    assert len(out) < len(huge)
    assert "truncated" in out


def test_code_reviewer_subagent_registered():
    """The code-reviewer subagent must register without crashing even
    when GROQ key is absent (registers disabled)."""
    from subagents.registry import clear_subagents, SUBAGENT_REGISTRY
    clear_subagents()
    from subagents.code_reviewer import register_code_reviewer
    register_code_reviewer()
    assert "code_reviewer" in SUBAGENT_REGISTRY
    spec = SUBAGENT_REGISTRY["code_reviewer"]
    assert spec.name == "code_reviewer"
    expected_enabled = bool(os.environ.get("GROQ_API_KEY"))
    assert spec.enabled is expected_enabled


def test_code_reviewer_factory_builds():
    """Tool factory must build cleanly — catches the same import
    regression class as the validator/browser_v2 task_done bug."""
    from subagents.registry import clear_subagents
    clear_subagents()
    from subagents.code_reviewer import (
        register_code_reviewer,
        _code_reviewer_tools,
    )
    register_code_reviewer()
    tools = _code_reviewer_tools()
    assert isinstance(tools, list) and len(tools) >= 1
    names = [getattr(getattr(t, "_func", t), "__name__", "") for t in tools]
    assert "review_code" in names


def test_code_reviewer_uses_groq_70b_for_depth():
    """Reviewer needs depth — should NOT default to the cheap 8b
    banter model. If someone 'optimizes' it down, review quality
    regresses sharply."""
    src = Path(tools.code_reviewer.__file__).read_text()
    assert "llama-3.3-70b" in src, (
        "code reviewer should default to llama-3.3-70b — depth matters"
    )
