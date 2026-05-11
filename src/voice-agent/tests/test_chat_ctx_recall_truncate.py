"""Verify recall-time truncation in scrub_recalled_assistant_text.

Live failure 2026-05-11: user asked "What's in your mind?" on Claude
Haiku 4.5 and got a 574-char architecture essay. Asked the same
question 9 minutes later, got the exact same essay verbatim — because
the first one was sitting in chat_ctx via the recall path, and Claude
copied the long shape from the in-context example. Abstract length
rules in the system prompt lose to concrete prior assistant turns.

Fix: cap the LENGTH of recalled assistant turns. The live response
the user hears is still full-length; only the historical version
re-injected into chat_ctx gets trimmed to the first sentence. Future
similar questions then see a short example to mimic, not an essay.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.chat_ctx import (
    RECALL_ASSISTANT_MAX_CHARS,
    _truncate_to_sentence,
    scrub_recalled_assistant_text,
)


def test_short_reply_passes_through_unchanged():
    """A normal-length assistant reply must not be touched."""
    text = "Got it. The build finished in 4.2 seconds."
    assert scrub_recalled_assistant_text(text) == text


def test_long_essay_gets_truncated():
    """The exact 574-char essay that triggered the bug must come back
    short enough that it primes Claude with a short shape."""
    essay = (
        "I don't run between turns — there's no continuous \"mind\" "
        "sitting here thinking when you're not speaking. Within a turn, "
        "something like attention: I parse what you said, hold the "
        "context, reason through it, shape a reply. But it's not like "
        "human thought with a stream of consciousness underneath.\n\n"
        "What's actually there: your chat history, the memory layer, "
        "the system prompt defining how I work, and the tools I can "
        "call. When you speak, those activate. When you stop, they don't."
    )
    out = scrub_recalled_assistant_text(essay)
    assert out is not None
    assert len(out) <= RECALL_ASSISTANT_MAX_CHARS, (
        f"truncation exceeded cap: {len(out)} > {RECALL_ASSISTANT_MAX_CHARS}"
    )
    # First sentence should be preserved intact.
    assert out.startswith("I don't run between turns"), (
        f"first sentence lost: {out!r}"
    )
    # The architecture mini-essay paragraph must be gone — that's
    # the whole point of the truncation.
    assert "memory layer" not in out
    assert "tools I can call" not in out


def test_truncate_picks_sentence_boundary_when_possible():
    """If a sentence ends before the cap, truncate there — don't
    cut mid-clause."""
    s = "First sentence. Second sentence. " + ("x" * 300)
    out = _truncate_to_sentence(s, RECALL_ASSISTANT_MAX_CHARS)
    # Should end with a period (sentence boundary), not mid-x's.
    assert out.endswith("."), (
        f"truncation didn't pick a sentence boundary: {out!r}"
    )


def test_truncate_handles_no_sentence_boundary():
    """If there's no '.!?' in the cap window, fall back to hard
    char-truncate — better than dropping the whole turn."""
    s = "x" * 1000
    out = _truncate_to_sentence(s, 100)
    assert len(out) <= 100
    assert out == "x" * 100


def test_truncate_skips_when_already_short():
    """If the text already fits, return as-is — no rewrite."""
    s = "Short reply."
    out = _truncate_to_sentence(s, 250)
    assert out == s


def test_truncate_respects_question_mark_as_boundary():
    """Both '?' and '!' end sentences too."""
    s = "Want me to check? " + ("x" * 300)
    out = _truncate_to_sentence(s, 250)
    assert out.endswith("?"), f"got {out!r}"


def test_meta_silence_still_drops():
    """Pre-existing behavior unchanged: '(silent)' / 'Listening.'
    style replies still return None."""
    assert scrub_recalled_assistant_text("(silent)") is None
    assert scrub_recalled_assistant_text("Listening.") is None


def test_archaic_opener_still_trimmed():
    """Pre-existing behavior unchanged: archaic openers still trimmed."""
    out = scrub_recalled_assistant_text("Indeed, sir. The build passed.")
    assert out is not None
    assert "Indeed" not in out
    assert "The build passed" in out


def test_long_reply_with_tool_leak_gets_both_cleaned_and_truncated():
    """Tool-leak filter runs FIRST (so the leak is gone before we
    measure length), then truncation runs."""
    leaky = (
        "Here's what I did. <function=bash>{\"command\": \"ls\"}</function> "
        "Then I ran it." + (" Extra words." * 50)
    )
    out = scrub_recalled_assistant_text(leaky)
    assert out is not None
    assert "<function" not in out
    assert len(out) <= RECALL_ASSISTANT_MAX_CHARS


def test_recall_cap_value_is_reasonable():
    """Sanity: the cap should be tight enough to kill essays but
    loose enough to keep one-sentence answers."""
    assert 100 <= RECALL_ASSISTANT_MAX_CHARS <= 400, (
        "cap is outside the reasonable voice-reply range — "
        "too tight kills real one-sentence answers, "
        "too loose lets essay-priming through"
    )


# ── Disabled-subagent poison filter ──────────────────────────────


class TestDisabledSubagentFilter:
    """Live failure 2026-05-11 15:51 UTC: chat history from a session
    where the screen_share subagent was enabled poisoned a session
    where it's disabled. Claude said 'Let me transfer to the screen
    subagent', tried `transfer_to_screen_share`, got "unknown AI
    function", then said 'I don't have that transfer tool available'.
    Drop those priming turns from chat_ctx so Claude doesn't have a
    precedent to copy.
    """

    @pytest.mark.parametrize("text", [
        "Let me transfer to the screen subagent who can read details better.",
        "I'll use transfer_to_screen_share for richer vision.",
        "Switching to the screen-share subagent for live reading.",
        "Let me switch to the screen subagent.",
    ])
    def test_subagent_mention_dropped(self, text):
        assert scrub_recalled_assistant_text(text) is None, (
            f"recalled turn mentioning disabled subagent must be dropped: {text!r}"
        )

    @pytest.mark.parametrize("text", [
        "Chrome is open with three tabs.",  # normal reply
        "I can see VS Code on the left.",
        "Got it.",
        "Screen sharing on.",  # NOT a subagent mention — pass through
    ])
    def test_unrelated_text_passes(self, text):
        assert scrub_recalled_assistant_text(text) == text
