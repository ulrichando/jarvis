"""Tests for cap_sir_count — the trailing-sir + body-sir filter
that runs over assistant text before TTS.

The user's repeated complaint: every JARVIS reply ended with ", sir."
which sounded robotic. As of 2026-05-09 (drop-butler-register
overhaul), the rule tightened from "keep one body sir" to "strip
every sir." We test:
  - Trailing-sir is always stripped, terminator preserved
  - ALL body-sirs are stripped (was: keep first, until 2026-05-09)
  - Bare-vocative ("Yes?") is exempt — bypasses this filter, and
    the canonical phrase no longer contains 'sir' anyway
  - Empty / no-sir inputs pass through unchanged
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from jarvis_agent import cap_sir_count


async def _stream(text: str):
    """Make an async iterator that yields one chunk."""
    yield text


def _run(text: str) -> str:
    """Push `text` through cap_sir_count and concatenate the output."""
    async def go():
        out = []
        async for chunk in cap_sir_count(_stream(text)):
            out.append(chunk)
        return "".join(out)
    return asyncio.run(go())


# ── Pass-through cases ──────────────────────────────────────────────


def test_no_sir_passes_through():
    assert _run("Done.") == "Done."


def test_empty_input_yields_nothing():
    assert _run("") == ""


# ── Trailing-sir stripping ──────────────────────────────────────────


def test_strips_trailing_sir_period():
    assert _run("Done, sir.") == "Done."


def test_strips_trailing_sir_exclaim():
    assert _run("Got it, sir!") == "Got it!"


def test_strips_trailing_sir_question():
    assert _run("Anything else, sir?") == "Anything else?"


def test_strips_trailing_sir_no_punctuation():
    """A reply ending with bare 'sir' (no terminator) — strip cleanly."""
    assert _run("Right sir") == "Right"


def test_strips_trailing_sir_no_comma():
    """'Yes sir.' (no comma) should also strip."""
    assert _run("Yes sir.") == "Yes."


def test_strips_trailing_sir_with_extra_whitespace():
    assert _run("Done,  sir  .") == "Done."


def test_strips_capitalized_sir_at_end():
    """STT sometimes capitalizes — case-insensitive match."""
    assert _run("It's clear, Sir.") == "It's clear."


def test_strips_when_only_word_after_period():
    """`Done. Sir.` — trailing 'Sir.' alone after a period is the
    most overtly robotic pattern. Strip it cleanly."""
    assert _run("Done. Sir.") == "Done."


# ── Body-sir handling: drop ALL (drop-butler-register 2026-05-09) ───


def test_drops_first_body_sir():
    """A mid-sentence sir is no longer kept — drop it too."""
    out = _run("Done, sir, and the file is saved.")
    assert "sir" not in out.lower()
    assert "Done" in out
    assert "the file is saved" in out


def test_drops_all_body_sirs():
    """Multiple body-sirs all get stripped (previously: kept first)."""
    out = _run("Done, sir, and the file is saved, sir, no errors.")
    assert "sir" not in out.lower()
    assert "Done" in out
    assert "the file is saved" in out
    assert "no errors" in out


def test_drops_body_and_trailing_sir():
    """Combined: body-sir AND trailing-sir → strip both."""
    out = _run("Done, sir. The file is saved, sir.")
    assert "sir" not in out.lower()
    assert "Done" in out
    assert "The file is saved" in out


# ── Edge cases ──────────────────────────────────────────────────────


def test_does_not_strip_sir_inside_word():
    """'sirloin' must not get its 'sir' stripped — \\b word boundary."""
    out = _run("I ordered sirloin.")
    assert "sirloin" in out


def test_yes_sir_question_form_stripped_too():
    """The canonical bare-vocative is now 'Yes?' (post-2026-05-09
    drop-butler-register). A legacy 'Yes, sir?' that somehow flows
    through this filter still gets stripped to 'Yes?' — same end
    state via the trailing-sir rule."""
    out = _run("Yes, sir?")
    assert out.strip() == "Yes?"
