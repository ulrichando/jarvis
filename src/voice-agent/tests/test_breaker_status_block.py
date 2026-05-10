# src/voice-agent/tests/test_breaker_status_block.py
"""Tests for the breaker-status injection into JARVIS_INSTRUCTIONS.

Audit recommendation F (2026-05-09): when an upstream Groq circuit
breaker opens (STT / TTS / LLM), the supervisor goes silent or
slow with no acknowledgment. Inject a system-status block into the
prompt so the LLM knows to say "Groq is slow tonight, switching to
DeepSeek" instead of leaving the user wondering.
"""
from __future__ import annotations

import pytest

from resilience.circuit_breaker import (
    CircuitBreaker, STATE_CLOSED, STATE_OPEN, STATE_HALF_OPEN,
)


@pytest.fixture
def breakers():
    """Three fresh breakers (STT/TTS/LLM)."""
    stt = CircuitBreaker("stt")
    tts = CircuitBreaker("tts")
    llm = CircuitBreaker("llm")
    return [stt, tts, llm]


def test_block_empty_when_all_breakers_closed(breakers):
    """All-healthy state must return an empty string (not even a
    header) so prompt size doesn't pay a cost when nothing's wrong."""
    from jarvis_agent import _build_breaker_status_block
    block = _build_breaker_status_block(breakers)
    assert block == "", (
        f"Expected empty block when all breakers closed, got {block!r}"
    )


def test_block_names_open_breakers(breakers):
    """When a single breaker is OPEN, the block names it explicitly so
    the LLM can give a specific acknowledgment ('Groq STT is slow')
    instead of a generic 'something's wrong'."""
    from jarvis_agent import _build_breaker_status_block
    breakers[2].state = STATE_OPEN  # llm
    block = _build_breaker_status_block(breakers)
    assert block, "Expected non-empty block when llm breaker is OPEN"
    assert "llm" in block.lower(), (
        f"Expected 'llm' named in block, got: {block!r}"
    )


def test_block_lists_multiple_open_breakers(breakers):
    """When >1 breaker is open, all of them appear in the block."""
    from jarvis_agent import _build_breaker_status_block
    breakers[0].state = STATE_OPEN  # stt
    breakers[2].state = STATE_OPEN  # llm
    block = _build_breaker_status_block(breakers)
    assert "stt" in block.lower()
    assert "llm" in block.lower()


def test_half_open_is_treated_as_degraded(breakers):
    """HALF-OPEN means the breaker is probing — still a degraded
    state from the user's perspective (they'll wait on the probe).
    Should appear in the block."""
    from jarvis_agent import _build_breaker_status_block
    breakers[1].state = STATE_HALF_OPEN  # tts
    block = _build_breaker_status_block(breakers)
    assert "tts" in block.lower(), (
        f"Half-open breaker should be reflected in block, got: {block!r}"
    )


def test_block_steers_llm_toward_specific_acknowledgment(breakers):
    """The injected text must give the LLM a concrete shape for the
    acknowledgment, not just say 'something's wrong'. The pre-2026-05-09
    behavior was silent retry; we want voiced acknowledgment."""
    from jarvis_agent import _build_breaker_status_block
    breakers[2].state = STATE_OPEN
    block = _build_breaker_status_block(breakers)
    # The block must include the user-facing-style guidance phrase
    # so the LLM has an example shape to mimic.
    block_lower = block.lower()
    has_guidance = any(
        marker in block_lower
        for marker in (
            "slower",
            "fallback",
            "acknowledge",
            "degrad",  # "degraded" / "degradation"
        )
    )
    assert has_guidance, (
        f"Expected user-acknowledgment guidance in block, got: {block!r}"
    )


def test_default_breakers_are_used_when_no_arg():
    """The function should default to reading the module's three
    breakers (_STT_BREAKER / _TTS_BREAKER / _LLM_BREAKER) so the
    on_user_turn_completed handler can call it with no arg."""
    import jarvis_agent
    # All three module breakers should be CLOSED by default in a fresh
    # import (no upstream calls have failed).
    block = jarvis_agent._build_breaker_status_block()
    assert block == "", (
        f"Expected empty block from default breakers (all closed), "
        f"got: {block!r}"
    )
