"""Track 1 — save/recall trigger regex (Spec 2026-05-24).

The regex is intentionally LIBERAL (supervisor LLM is the second gate).
These tests pin the documented behaviour table.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_VOICE_AGENT_ROOT = Path(__file__).resolve().parent.parent
if str(_VOICE_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_VOICE_AGENT_ROOT))


# Save trigger — true positives
@pytest.mark.parametrize("text", [
    "Jarvis, remember this: I prefer fish.",
    "Jarvis remember that I'm allergic to fish",
    "Could you save that for me?",
    "Don't forget I prefer terse replies",
    "remember me to call the bank",
    "Save this process: deploy = run tests then push",
    "Memorize this for next time",
    "write this down please",
])
def test_save_trigger_matches(text):
    from jarvis_agent import _SAVE_TRIGGER_RE
    assert _SAVE_TRIGGER_RE.search(text), f"should match: {text!r}"


@pytest.mark.parametrize("text", [
    "I'll always remember that joke",  # liberal match — supervisor decides
])
def test_save_trigger_liberal_matches_acknowledged(text):
    """Regex IS liberal; supervisor LLM is the 2nd gate."""
    from jarvis_agent import _SAVE_TRIGGER_RE
    assert _SAVE_TRIGGER_RE.search(text)


@pytest.mark.parametrize("text", [
    "This song is unforgettable",
    "Remember when we did the deploy?",
    "what is memory?",
    "",
])
def test_save_trigger_does_not_match(text):
    from jarvis_agent import _SAVE_TRIGGER_RE
    assert not _SAVE_TRIGGER_RE.search(text), f"should NOT match: {text!r}"


@pytest.mark.parametrize("text", [
    "Do you remember when we talked about Shelby?",
    "What did I tell you about my allergies?",
    "Have I told you about my project?",
    "Remind me about the deploy procedure",
    "remind me of my morning routine",
    "remind me what I said about coffee",
])
def test_recall_trigger_matches(text):
    from jarvis_agent import _RECALL_TRIGGER_RE
    assert _RECALL_TRIGGER_RE.search(text), f"should match: {text!r}"


@pytest.mark.parametrize("text", [
    "I don't remember",
    "Remember this: ...",
])
def test_recall_trigger_does_not_match(text):
    from jarvis_agent import _RECALL_TRIGGER_RE
    assert not _RECALL_TRIGGER_RE.search(text), f"should NOT match: {text!r}"
