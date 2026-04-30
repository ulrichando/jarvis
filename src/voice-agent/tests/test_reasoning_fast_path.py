"""Tests for `_REASONING_FAST_PATH_RE` — the synchronous regex
pre-classifier for high-confidence reasoning prompts.

Phase 9.1 of /loop voice-intelligence: live telemetry over 127 turns
recorded zero REASONING-route activity. Either the LLM classifier
was collapsing reasoning prompts onto TASK or the user never asked
any. This regex forces REASONING when the prompt has an unambiguous
reasoning shape (explain, why does, walk me through, design, debug,
compare X to Y), giving us telemetry data + ensuring qwen3-32b is
used where it's actually suited.

Critical disambiguation: must NOT match the BANTER 'how are you'
family. BANTER asks about JARVIS; REASONING asks about a topic.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from jarvis_agent import _REASONING_FAST_PATH_RE


def _matches(text: str) -> bool:
    return bool(_REASONING_FAST_PATH_RE.match(text))


# ── Should match (REASONING) ──────────────────────────────────────────


def test_why_does_questions():
    assert _matches("why does http work")
    assert _matches("why does the sky look blue")
    assert _matches("why is recursion slow")
    assert _matches("why are floats imprecise")
    assert _matches("why would someone use a linked list")
    assert _matches("why should I care about this")
    assert _matches("why can't I divide by zero")


def test_how_does_x_work():
    assert _matches("how does http work")
    assert _matches("how does tcp work")
    assert _matches("how do react hooks work")
    assert _matches("how do hash tables work")
    assert _matches("how does the gc work")


def test_explain_walk_me_through():
    assert _matches("explain recursion")
    assert _matches("explain how dns works")
    assert _matches("walk me through the algorithm")
    assert _matches("walk me through the deployment")
    assert _matches("tell me how oauth works")
    assert _matches("can you explain pointers")


def test_engineering_verbs():
    assert _matches("design a rate limiter")
    assert _matches("debug this function")
    assert _matches("trace through the call stack")
    assert _matches("architect a notification system")


def test_compare_and_difference():
    assert _matches("what's the difference between http and https")
    assert _matches("compare arrays to linked lists")
    assert _matches("compare python with go")


def test_step_by_step():
    assert _matches("step by step explain how to do this")
    assert _matches("step-by-step walk me through deployment")


def test_why_modal_questions():
    assert _matches("why would I use a hashmap here")
    assert _matches("why should this be in a separate module")
    assert _matches("why might this fail under load")


def test_case_insensitive():
    assert _matches("EXPLAIN recursion")
    assert _matches("Why Does HTTP Work")


# ── Must NOT match (BANTER / TASK / EMOTIONAL territory) ─────────────


def test_banter_how_are_you_doesnt_match():
    """REASONING regex MUST NOT eat the BANTER 'how are you' family.
    These prompts ask about JARVIS, not a topic."""
    assert not _matches("how are you")
    assert not _matches("how's it going")
    assert not _matches("how you doing")
    assert not _matches("how have you been")
    assert not _matches("how are you doing today")


def test_greetings_dont_match():
    assert not _matches("hey jarvis")
    assert not _matches("hello there")
    assert not _matches("good morning")
    assert not _matches("yo")


def test_action_commands_dont_match():
    """TASK route — direct UI / system actions, not reasoning."""
    assert not _matches("open chrome")
    assert not _matches("take a screenshot")
    assert not _matches("play music")
    assert not _matches("what time is it")


def test_emotional_doesnt_match():
    assert not _matches("i'm so frustrated")
    assert not _matches("i feel terrible today")


def test_ambiguous_short_questions_dont_match():
    """Short questions without reasoning verbs SHOULDN'T trip the
    REASONING fast-path — let the classifier decide."""
    assert not _matches("what's that")
    assert not _matches("is it working")
    assert not _matches("can you do it")


def test_empty():
    assert not _matches("")
    assert not _matches("   ")


def test_explain_needs_a_topic():
    """'Explain' alone doesn't reach the regex — needs a follow-up word."""
    assert not _matches("explain")
    assert not _matches("explain.")
    # But with a topic it should
    assert _matches("explain everything")


def test_walk_me_through_needs_a_topic():
    assert not _matches("walk me through")
    assert _matches("walk me through it")


def test_why_alone_doesnt_match():
    """Bare 'why' / 'why?' isn't enough — needs the verb structure."""
    assert not _matches("why")
    assert not _matches("why?")
    # But the structured forms do
    assert _matches("why does that fail")
