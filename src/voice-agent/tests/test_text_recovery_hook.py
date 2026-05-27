"""Tests for pipeline.text_recovery_detect — the pure content-block
inspector that decides whether an assistant item triggered the
silent-end-of-turn failure mode."""
from __future__ import annotations

import pytest


def _text_block(s):
    """Mimic a livekit-agents text content block."""
    class _T:
        type = "text"
        text = s
    return _T()


def _tool_use_block():
    class _TU:
        type = "tool_use"
    return _TU()


def test_item_with_text_and_tool_use_is_interstitial():
    """ack-text + tool_use is the FIRST iteration of a tool-chain turn.
    Don't trigger recovery; the followup hasn't run yet."""
    from pipeline.text_recovery_detect import classify_assistant_item
    cls = classify_assistant_item(
        content=[_text_block("Looking into that."), _tool_use_block()],
        had_prior_tool_calls=False,
    )
    assert cls == "interstitial"


def test_item_with_only_tool_use_is_interstitial():
    """Pure tool_use (silent chain step) is also interstitial."""
    from pipeline.text_recovery_detect import classify_assistant_item
    cls = classify_assistant_item(
        content=[_tool_use_block()],
        had_prior_tool_calls=True,
    )
    assert cls == "interstitial"


def test_item_with_only_text_and_no_prior_tools_is_final():
    """Pure text reply, no tools fired this turn — normal BANTER-shaped turn."""
    from pipeline.text_recovery_detect import classify_assistant_item
    cls = classify_assistant_item(
        content=[_text_block("Hi there.")],
        had_prior_tool_calls=False,
    )
    assert cls == "final_reply"


def test_item_with_only_text_after_tools_is_final():
    """Pure text reply AFTER tools fired — this is the happy path: tool
    chain ran, LLM emitted summary text."""
    from pipeline.text_recovery_detect import classify_assistant_item
    cls = classify_assistant_item(
        content=[_text_block("I found three changes.")],
        had_prior_tool_calls=True,
    )
    assert cls == "final_reply"


def test_empty_item_after_tools_is_silent_failure():
    """No text, no tool_use, BUT tools fired earlier this turn → the
    LLM produced an empty reply where it should have summarized.
    This is the failure mode the recovery path is for."""
    from pipeline.text_recovery_detect import classify_assistant_item
    cls = classify_assistant_item(
        content=[],
        had_prior_tool_calls=True,
    )
    assert cls == "silent_failure"


def test_empty_item_with_no_prior_tools_is_benign_skip():
    """Empty item AND no tool calls — degenerate but not a failure of
    'forgot to voice the result' (nothing was being processed). Skip."""
    from pipeline.text_recovery_detect import classify_assistant_item
    cls = classify_assistant_item(
        content=[],
        had_prior_tool_calls=False,
    )
    assert cls == "benign_empty"


def test_whitespace_only_text_after_tools_is_silent_failure():
    """Text block that's just whitespace doesn't count as a real reply."""
    from pipeline.text_recovery_detect import classify_assistant_item
    cls = classify_assistant_item(
        content=[_text_block("   \n  ")],
        had_prior_tool_calls=True,
    )
    assert cls == "silent_failure"


def test_string_content_supported():
    """Some livekit-agents builds pass content as a list of plain strings
    instead of typed blocks. Detector must handle both shapes."""
    from pipeline.text_recovery_detect import classify_assistant_item
    cls = classify_assistant_item(
        content=["I found the answer."],
        had_prior_tool_calls=True,
    )
    assert cls == "final_reply"


def test_dict_content_supported():
    """Some shapes use dict-style {'type': 'tool_use'} or {'type':'text','text':'…'}."""
    from pipeline.text_recovery_detect import classify_assistant_item
    cls = classify_assistant_item(
        content=[
            {"type": "text", "text": "Looking…"},
            {"type": "tool_use", "name": "computer_use"},
        ],
        had_prior_tool_calls=False,
    )
    assert cls == "interstitial"


def test_none_content_treated_as_empty():
    """item.content=None must not crash the classifier."""
    from pipeline.text_recovery_detect import classify_assistant_item
    cls = classify_assistant_item(
        content=None,
        had_prior_tool_calls=True,
    )
    assert cls == "silent_failure"
