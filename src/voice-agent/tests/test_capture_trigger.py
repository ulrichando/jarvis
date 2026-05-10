# src/voice-agent/tests/test_capture_trigger.py
"""Tests for the deterministic capture-trigger matcher.

Audit recommendation E (2026-05-09): the supervisor's PROACTIVE
CAPTURE prompt section was being ignored — JARVIS only saved 3
memories in 285 sessions. Move the trigger phrases into a sync
regex so capture happens deterministically, bypassing both the
LLM tool-choice surface and the auto-extractor's LLM judgment.

Mirrors the design of `is_recall_query` (Layer 2 force-router).
"""
from __future__ import annotations
import pytest
from pipeline.turn_router import detect_capture_trigger


# ── Should fire (positive cases drawn from the audit's examples) ──────

@pytest.mark.parametrize("transcript,expected_category", [
    # Pricing — verbatim from the 2026-05-08 Coding Kiddos live failure
    ("we charge them $600 for six months", "project"),
    ("we charge $600 for 6 months", "project"),
    ("I charge $100 a month per student", "project"),
    ("the rate is $50 per session", "project"),
    # Offering / what the business does
    ("we are teaching Python, JavaScript, Lua", "project"),
    ("we teach kids to code", "project"),
    ("I teach Python and JavaScript", "project"),
    ("we build mobile apps for clients", "project"),
    ("we sell coding courses online", "project"),
    ("we offer one-on-one tutoring", "project"),
    # Operational scale
    ("I have 20 students this semester", "project"),
    ("we have 50 customers right now", "project"),
    ("I have three clients in Cameroon", "project"),
    # Role / responsibility
    ("I run Pretva, a ride-hailing service", "user"),
    ("I founded Coding Kiddos last year", "user"),
    ("I built JARVIS as my personal assistant", "user"),
    # Location
    ("I live in Cameroon", "user"),
    ("I'm in Yaoundé this week", "user"),
])
def test_capture_triggers_fire(transcript, expected_category):
    """Each recognized pattern returns a (category, content) tuple."""
    result = detect_capture_trigger(transcript)
    assert result is not None, (
        f"Expected capture trigger to fire on {transcript!r}, got None"
    )
    category, content = result
    assert category == expected_category, (
        f"Expected category={expected_category!r} for {transcript!r}, "
        f"got {category!r}"
    )
    assert content, "Captured content must be non-empty"
    assert len(content) >= len(transcript.split()[0]), (
        "Content too short — should preserve the user's quote or a fact-shape"
    )


# ── Should NOT fire (false-positive guard) ────────────────────────────

@pytest.mark.parametrize("transcript", [
    # Empty / whitespace
    "",
    "   ",
    # Greetings + acks (caught by other gates)
    "hello",
    "yes",
    "okay",
    "thanks",
    # Imperatives / commands (not facts)
    "charge my phone",                  # "charge" but not "we/I charge X"
    "remember to bring milk",
    "teach me Python",                  # imperative, not "we teach X"
    "build me a website",               # imperative, not "we build X"
    # Recall queries (those go through is_recall_query, not capture)
    "do you remember my name",
    "what did I tell you about pricing",
    # Ephemeral / one-time state (audit explicitly excludes)
    "I'm hungry right now",
    "today I'm working on the demo",
    "I just woke up",
    # Past-tense narrative without durable fact-shape
    "I went to the store yesterday",
    "we had lunch at noon",
    # Generic statements that aren't user-facts
    "the sky is blue",
    "Postgres uses MVCC",
])
def test_capture_triggers_dont_fire_on_non_facts(transcript):
    """Imperatives, recall queries, ephemeral state, and generic
    statements must NOT trigger a capture."""
    result = detect_capture_trigger(transcript)
    assert result is None, (
        f"Expected NO capture trigger for {transcript!r}, got {result!r}"
    )


# ── Captured content shape ────────────────────────────────────────────

def test_capture_content_includes_user_quote():
    """The returned content should preserve the user's actual phrasing
    (or a close paraphrase) so the saved memory is faithful."""
    result = detect_capture_trigger("we charge $600 for six months")
    assert result is not None
    _, content = result
    assert "$600" in content, (
        f"Expected $600 in captured content, got {content!r}"
    )


def test_capture_content_for_role_includes_business_name():
    """For 'I run X' patterns, the captured content should name X."""
    result = detect_capture_trigger("I run Pretva, a ride-hailing service")
    assert result is not None
    _, content = result
    assert "Pretva" in content, (
        f"Expected 'Pretva' in captured content, got {content!r}"
    )


# ── Edge cases ────────────────────────────────────────────────────────

def test_none_input_returns_none():
    assert detect_capture_trigger(None) is None  # type: ignore[arg-type]


def test_empty_string_returns_none():
    assert detect_capture_trigger("") is None
