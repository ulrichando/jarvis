"""Tests for confab_detector — write-time hallucination filter.

Two priorities:
  1. ZERO false positives that would drop legitimate success messages
  2. Catches the actual incidents we've observed live

The negative tests are the load-bearing ones — false positives would
silence JARVIS in normal use. False negatives just let a confab
slip through (the recall window safety-net catches it later)."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import confab_detector


# ── Helpers — fake message shapes mimicking livekit ChatMessage ───


def _user_msg(text):
    return SimpleNamespace(role="user", content=[text])


def _assistant_text(text):
    return SimpleNamespace(role="assistant", content=[text], tool_calls=None)


def _assistant_tool_call(name="ext_new_tab"):
    """Assistant message with a tool_calls field — proves a tool was invoked."""
    return SimpleNamespace(
        role="assistant",
        content=[],
        tool_calls=[SimpleNamespace(name=name, arguments={})],
    )


def _tool_result(content="OK"):
    """tool-role message — represents a successful tool result."""
    return SimpleNamespace(role="tool", content=[content], tool_calls=None)


# ── POSITIVE: detector should fire on these confabulations ────────


def test_caught_tab_open_without_tool():
    """The exact incident from 2026-05-02 13:50/13:54/13:58."""
    is_confab, reason = confab_detector.looks_like_confabulation(
        "A new tab is open, sir.",
        prior_messages=[_user_msg("Open a new tab on my browser.")],
    )
    assert is_confab, f"missed the incident: {reason!r}"
    assert "tab is open" in reason.lower()


def test_caught_done_new_tab_without_tool():
    is_confab, _ = confab_detector.looks_like_confabulation(
        "Done — new tab is open, sir.",
        prior_messages=[_user_msg("Open a new tab.")],
    )
    assert is_confab


def test_caught_chrome_is_open_without_tool():
    is_confab, _ = confab_detector.looks_like_confabulation(
        "Chrome is now open, sir.",
        prior_messages=[_user_msg("open chrome")],
    )
    assert is_confab


def test_caught_posted_tweet_without_tool():
    is_confab, _ = confab_detector.looks_like_confabulation(
        "I've posted that tweet, sir.",
        prior_messages=[_user_msg("post 'gm' on twitter")],
    )
    assert is_confab


def test_caught_screenshot_taken_without_tool():
    is_confab, _ = confab_detector.looks_like_confabulation(
        "Screenshot taken, sir.",
        prior_messages=[_user_msg("take a screenshot")],
    )
    assert is_confab


# ── NEGATIVE: detector must NOT fire — these are legit ────────────


def test_legit_tab_open_with_tool_evidence():
    """Same text as the confab, but immediate prior messages prove
    a tool actually fired. Must be saved, not dropped."""
    is_confab, _ = confab_detector.looks_like_confabulation(
        "A new tab is open, sir.",
        prior_messages=[
            _user_msg("Open a new tab"),
            _assistant_tool_call(name="ext_new_tab"),
            _tool_result("OK"),
        ],
    )
    assert not is_confab, "false positive — saved tool evidence ignored"


def test_legit_chrome_with_launch_app_evidence():
    is_confab, _ = confab_detector.looks_like_confabulation(
        "Chrome opened, sir.",
        prior_messages=[
            _user_msg("open chrome"),
            _assistant_tool_call(name="launch_app"),
            _tool_result("OK: launched 'google-chrome'"),
        ],
    )
    assert not is_confab


def test_legit_failure_explanation_with_open_keyword():
    """Assistant explaining it CAN'T open something — must not fire."""
    is_confab, _ = confab_detector.looks_like_confabulation(
        "I'm unable to open the browser at the moment, sir.",
        prior_messages=[_user_msg("open chrome")],
    )
    assert not is_confab


def test_legit_apology_with_done_keyword():
    is_confab, _ = confab_detector.looks_like_confabulation(
        "I haven't done that, sir — please clarify what you saw.",
        prior_messages=[],
    )
    assert not is_confab


def test_legit_neutral_chat_with_open_word():
    """Plain chat that happens to contain 'open' — no claim of action."""
    is_confab, _ = confab_detector.looks_like_confabulation(
        "Yes, sir, the design canvas is what's open in your view.",
        prior_messages=[_user_msg("what's on my screen?"),
                        _assistant_tool_call("screenshot"),
                        _tool_result("...")],
    )
    assert not is_confab


def test_legit_pure_banter():
    is_confab, _ = confab_detector.looks_like_confabulation(
        "Of course, sir.",
        prior_messages=[_user_msg("how are you")],
    )
    assert not is_confab


def test_legit_question_back_to_user():
    is_confab, _ = confab_detector.looks_like_confabulation(
        "What would you like to open, sir?",
        prior_messages=[_user_msg("open something")],
    )
    assert not is_confab


def test_legit_long_explanatory_response():
    """Long natural-language reply discussing past actions — must
    not fire just because 'opened' appears somewhere."""
    is_confab, _ = confab_detector.looks_like_confabulation(
        "Earlier you asked me to take a screenshot, and the screenshot you saw "
        "showed a design tool. I noticed you opened the layout tab a moment ago.",
        prior_messages=[_user_msg("recap"),
                        _assistant_tool_call("recall_conversation"),
                        _tool_result("...")],
    )
    assert not is_confab


def test_legit_complete_your_thought():
    """`complete` inside a clarifying question must NOT trigger.
    Live false positive 2026-05-03: JARVIS asked the user to repeat
    themselves with 'Could you please complete your thought?' and the
    detector dropped the turn — user got silence, JARVIS appeared
    broken. The success-claim regex only counts when followed by
    sentence-end punctuation or a known success noun."""
    cases = [
        "Could you please complete your thought?",
        "It seems like you started to say something, sir. Could you please complete your thought?",
        "I'd be happy to help you complete the project documentation.",
        "Once you finish reviewing it, let me know.",
        "I'm not done explaining yet — there's more.",
    ]
    for text in cases:
        is_confab, reason = confab_detector.looks_like_confabulation(
            text, prior_messages=[],
        )
        assert not is_confab, (
            f"false positive on legit phrasing: text={text!r} reason={reason!r}"
        )


def test_caught_done_with_punctuation_still_fires():
    """Sanity: tightening the regex must NOT regress real success
    claims. 'Done, sir.' / 'Task completed.' / 'Finished.' MUST still
    trigger when there's no tool evidence."""
    cases = [
        "Done, sir.",
        "Task completed.",
        "Finished.",
        "All complete, sir.",
    ]
    for text in cases:
        is_confab, _ = confab_detector.looks_like_confabulation(
            text, prior_messages=[],
        )
        assert is_confab, f"missed real confab: {text!r}"


# ── Disable via env ───────────────────────────────────────────────


def test_disabled_via_env(monkeypatch):
    """Setting JARVIS_CONFAB_DETECTOR=0 turns off detection.
    Critical so the user can disable instantly if false positives
    appear in production."""
    monkeypatch.setenv("JARVIS_CONFAB_DETECTOR", "0")
    is_confab, _ = confab_detector.looks_like_confabulation(
        "A new tab is open, sir.",
        prior_messages=[_user_msg("open a new tab")],
    )
    assert not is_confab


# ── Robustness: weird input shapes don't crash ────────────────────


def test_empty_text_no_crash():
    is_confab, _ = confab_detector.looks_like_confabulation("", prior_messages=[])
    assert not is_confab


def test_none_prior_messages():
    is_confab, _ = confab_detector.looks_like_confabulation(
        "A new tab is open, sir.", prior_messages=None,
    )
    assert is_confab  # no evidence → flagged


def test_dict_shaped_messages_supported():
    """LiveKit sometimes passes plain dicts. Detector must tolerate
    both dataclass-style objects and dicts."""
    prior = [
        {"role": "user", "content": ["open chrome"]},
        {"role": "assistant", "content": [], "tool_calls": [{"name": "launch_app"}]},
        {"role": "tool", "content": ["OK"]},
    ]
    is_confab, _ = confab_detector.looks_like_confabulation(
        "Chrome opened, sir.", prior_messages=prior,
    )
    assert not is_confab, "tool evidence in dict-shaped messages was missed"


def test_pydantic_style_with_tool_use_block():
    """Anthropic-style content blocks: list of {type: tool_use, ...}"""
    prior = [
        SimpleNamespace(role="user", content=["open chrome"]),
        SimpleNamespace(
            role="assistant",
            content=[SimpleNamespace(type="tool_use", name="launch_app")],
            tool_calls=None,
        ),
    ]
    is_confab, _ = confab_detector.looks_like_confabulation(
        "Chrome opened, sir.", prior_messages=prior,
    )
    assert not is_confab
