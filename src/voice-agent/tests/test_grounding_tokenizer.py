"""Past-tense success-claim tokenizer — extracts (verb, object) pairs
from supervisor draft text. The grounding gate matches each pair
against the blackboard."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.mark.parametrize("text,expected_verbs", [
    ("I've opened a new tab.", ["opened"]),
    ("Tab is open.", ["open"]),
    ("Saved the file.", ["saved"]),
    ("Sent the email.", ["sent"]),
    ("Posted the tweet.", ["posted"]),
    ("Done.", ["done"]),
    ("I've launched Chrome and navigated to YouTube.", ["launched", "navigated"]),
    ("Created the new file.", ["created"]),
    ("Deleted that line for you.", ["deleted"]),
    ("Clicked the cancel button.", ["clicked"]),
])
def test_extract_claims_finds_past_tense_verbs(text, expected_verbs):
    from supervisor_graph.grounding_gate import extract_claims
    claims = extract_claims(text)
    found_verbs = [c.verb for c in claims]
    for v in expected_verbs:
        assert v in found_verbs, (
            f"text={text!r} expected verb {v!r}; got {found_verbs!r}"
        )


@pytest.mark.parametrize("text", [
    "What would you like me to do?",
    "I can open a tab — should I?",
    "How are you?",
    "I'll save it after you confirm.",
    "It's a sunny day.",
    "Let me check.",
    "One moment.",
])
def test_extract_claims_ignores_non_completion_text(text):
    from supervisor_graph.grounding_gate import extract_claims
    claims = extract_claims(text)
    assert claims == [], (
        f"text={text!r} should produce no claims; got {claims!r}"
    )


def test_extract_claims_captures_object_keywords():
    """The object keywords give the gate something to match against."""
    from supervisor_graph.grounding_gate import extract_claims
    claims = extract_claims("I've opened a new tab in Chrome.")
    assert len(claims) >= 1
    c = claims[0]
    assert c.verb == "opened"
    # Keywords should include 'tab' and 'chrome' (lowercased).
    assert "tab" in c.keywords or "chrome" in c.keywords
