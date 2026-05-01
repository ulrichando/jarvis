"""Tests for cap_sir_count — the trailing-sir trimmer + at-most-one-sir
gate that runs over assistant text before TTS.

The user's repeated complaint: every JARVIS reply ends with ", sir."
which sounds robotic. cap_sir_count is the post-process filter that
shapes this. We test:
  - Trailing-sir is always stripped, terminator preserved
  - Body-sir is kept (at most once) — natural mid-sentence sir is fine
  - Bare-vocative ("Yes, sir?") is exempt (bypasses this filter)
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


# ── Body-sir handling (at most one) ─────────────────────────────────


def test_keeps_first_body_sir():
    """A natural mid-sentence sir is OK to keep."""
    out = _run("Done, sir, and the file is saved.")
    # First 'sir' kept, no trailing-sir to strip
    assert ", sir," in out
    assert out.count("sir") == 1


def test_drops_second_body_sir():
    out = _run("Done, sir, and the file is saved, sir, no errors.")
    # Two body-sirs — keep first, drop second.
    assert out.count("sir") == 1


def test_keeps_one_body_sir_strips_trailing():
    """Combined: a body-sir AND a trailing-sir → keep body, drop trailing."""
    out = _run("Done, sir. The file is saved, sir.")
    assert out.count("sir") == 1
    assert not out.rstrip().endswith("sir.")


# ── Edge cases ──────────────────────────────────────────────────────


def test_does_not_strip_sir_inside_word():
    """'sirloin' must not get its 'sir' stripped — \\b word boundary."""
    out = _run("I ordered sirloin.")
    assert "sirloin" in out


def test_yes_sir_question_form_stripped_too():
    """The bare-vocative 'Yes, sir?' goes through session.say() directly,
    not through cap_sir_count. But if it ever did flow through this
    filter, the trailing-sir rule would strip it. That's acceptable —
    the canonical bare-vocative path bypasses this entirely."""
    out = _run("Yes, sir?")
    # Trailing-sir strip catches it; output is "Yes?"
    assert out.strip() == "Yes?"
