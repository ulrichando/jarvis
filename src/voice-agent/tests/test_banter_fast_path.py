"""Tests for `_BANTER_FAST_PATH_RE` — the synchronous regex pre-classifier
that lets high-confidence chitchat skip the 500ms Groq router and swap
to the fast inner LLM before the framework's reply pipeline reads
session._llm.

Why this matters: iteration-1 telemetry showed BANTER median TTFW = 4.8s
even though BANTER is *supposed* to be the snappiest route. Root cause:
the async classifier swap landed AFTER the framework had already started
the LLM call on the previous turn's _llm. Iteration-2 fix is the regex
fast-path; this file pins the patterns we want hit and avoid.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from jarvis_agent import _BANTER_FAST_PATH_RE


def _matches(text: str) -> bool:
    """Match condition matches the dispatcher: ≤6 words AND regex match."""
    return len(text.split()) <= 6 and bool(_BANTER_FAST_PATH_RE.match(text))


# ── Should match (fast-path) ──────────────────────────────────────────


def test_greetings_match():
    assert _matches("hey")
    assert _matches("hi")
    assert _matches("hello")
    assert _matches("yo jarvis")
    assert _matches("hey jarvis")
    assert _matches("hello there")
    assert _matches("howdy")
    assert _matches("good morning")
    assert _matches("good morning jarvis")
    assert _matches("good night sir")


def test_how_are_you_family_matches():
    assert _matches("how are you")
    assert _matches("how's it going")
    assert _matches("how you doing")
    assert _matches("how have you been")
    assert _matches("how's it going jarvis")


def test_casual_affirmations_match():
    assert _matches("thanks")
    assert _matches("thank you")
    assert _matches("cool")
    assert _matches("nice")
    assert _matches("awesome")
    assert _matches("perfect")
    assert _matches("got it")
    assert _matches("got it sir")
    assert _matches("alright")
    assert _matches("sounds good")


def test_signoffs_match():
    assert _matches("bye")
    assert _matches("goodbye")
    assert _matches("see you")
    assert _matches("see ya later")
    assert _matches("good night")
    assert _matches("catch you later")


def test_chitchat_openers_match():
    assert _matches("tell me a joke")
    assert _matches("tell me another joke")
    assert _matches("any news")
    assert _matches("what's up")
    assert _matches("what's new")
    assert _matches("i'm back")
    assert _matches("i'm bored")


def test_punctuation_tolerance():
    # Trailing punctuation must not break the match (Whisper transcripts
    # sometimes include them, sometimes not).
    assert _matches("hey jarvis!")
    assert _matches("good morning.")
    assert _matches("thanks!")
    assert _matches("how are you?")


def test_case_insensitive():
    assert _matches("HEY JARVIS")
    assert _matches("Good Morning")
    assert _matches("THANKS")


# ── Should NOT match (fall through to classifier) ────────────────────


def test_action_requests_dont_match():
    # These need the classifier and the TASK route — NOT BANTER.
    assert not _matches("open chrome")
    assert not _matches("take a screenshot")
    assert not _matches("hey jarvis open chrome")
    assert not _matches("send a message to mom")
    assert not _matches("what time is it")
    assert not _matches("what's the weather")
    assert not _matches("play music")


def test_questions_about_topics_dont_match():
    # "How does X work" is a REASONING turn, not chitchat.
    assert not _matches("how does http work")
    assert not _matches("why is the sky blue")
    assert not _matches("explain recursion")


def test_emotional_content_doesnt_match():
    # EMOTIONAL turns need their own LLM and voice — must not be
    # collapsed onto BANTER.
    assert not _matches("i'm so frustrated")
    assert not _matches("i feel terrible")
    assert not _matches("i don't know what to do")


def test_long_sentences_dont_match():
    # Word-cap safety: even if a long sentence STARTS with "hey jarvis",
    # we don't want to pre-empt the classifier.
    assert not _matches(
        "hey jarvis can you check the deployment status on production"
    )
    assert not _matches(
        "good morning, what's on my calendar today and any urgent emails"
    )


def test_empty_and_whitespace_dont_match():
    assert not _matches("")
    assert not _matches("   ")


def test_bare_vocative_doesnt_collide():
    # "jarvis" alone is handled upstream by `_BARE_VOCATIVE_RE` in
    # `on_user_turn_completed`, which raises StopResponse — the dispatch
    # listener never sees it. So the BANTER regex doesn't have to reject
    # bare-vocative explicitly. We DO want "hey jarvis" to fast-path as
    # BANTER though (greeting). The two regexes coexist because
    # on_user_turn_completed runs first; if it short-circuits, nothing
    # else runs.
    # For belt-and-suspenders, the BANTER regex anyway requires a
    # greeting/affirmation word — bare "jarvis" by itself doesn't match.
    assert not _matches("jarvis")
    # "hey jarvis" is chitchat → fast-path expected
    assert _matches("hey jarvis.")
