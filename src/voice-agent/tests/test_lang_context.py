"""LangContext — per-session most-recent-detected user language.

Confidence floor protects against 1-word utterances ("hi" / "merci")
bouncing the voice — STT's language ID isn't reliable on tiny inputs.
"""
from __future__ import annotations

from pipeline.lang_context import LangContext


def test_default_is_english():
    ctx = LangContext()
    assert ctx.get() == "en"


def test_default_override():
    ctx = LangContext(default="fr")
    assert ctx.get() == "fr"


def test_set_above_floor_sticks():
    ctx = LangContext()
    ctx.set("fr", confidence=0.9)
    assert ctx.get() == "fr"


def test_set_below_floor_is_noop():
    """Confidence below the floor (0.6) does not update.

    Short utterances ("hi" / "merci") often have low-confidence
    detection that flip-flops; the floor keeps the voice steady."""
    ctx = LangContext()
    ctx.set("fr", confidence=0.5)
    assert ctx.get() == "en"  # unchanged


def test_set_at_floor_sticks():
    ctx = LangContext()
    ctx.set("fr", confidence=0.6)
    assert ctx.get() == "fr"


def test_multiple_updates_track_latest():
    ctx = LangContext()
    ctx.set("fr", confidence=0.9)
    ctx.set("en", confidence=0.95)
    ctx.set("fr", confidence=0.8)
    assert ctx.get() == "fr"


def test_default_confidence_is_max():
    """set() called without confidence keyword arg should accept the
    update (used by callers that don't have per-event confidence)."""
    ctx = LangContext()
    ctx.set("fr")
    assert ctx.get() == "fr"
