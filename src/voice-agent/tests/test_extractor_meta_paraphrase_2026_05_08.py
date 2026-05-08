"""Memory extractor meta-paraphrase reject filter (fix D in the
2026-05-08 audit).

Live captures of polluting extractions:
  [extractor] reference: 'The English language is a West Germanic language...'
  [extractor] project:   'The user inquires about a working group for...'
  [extractor] project:   'The conversation has shifted to a casual topic...'
  [extractor] feedback:  'It seems to be a mixed review of a product...'
  [extractor] project:   'Coding Kiddos appears to involve a simulation or game...'

Filter rejects these LLM-meta narration outputs without the LLM
needing to retry. Genuine facts ("Ulrich's wife is Lizzy") still pass.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.memory_extractor import (
    parse_extractor_output,
    _is_meta_paraphrase,
)


# ── Reject: meta-paraphrase shapes ───────────────────────────────────


@pytest.mark.parametrize("content", [
    "The user is asking about the history of England.",
    "The user appears to be requesting mute.",
    "The user inquires about a working group for additional health costs.",
    "The user expresses gratitude and openness to emotional expression.",
    "The user expressed gratitude for the time spent.",
    "The conversation has shifted to a casual topic about a bird.",
    "The conversation appears to have ended with a positive note.",
    "The discussion is about climate policy.",
    "It seems to be a mixed review of a product or service.",
    "It appears to be a question about scheduling.",
    "User appears to be requesting mute.",
    "User wants to know about the weather.",
    "User asked about the price of GPUs.",
    "the user seeks information about Python.",
])
def test_meta_paraphrase_rejected(content):
    """`_is_meta_paraphrase` must return True for narration shapes."""
    assert _is_meta_paraphrase(content), (
        f"meta-paraphrase not detected: {content!r}"
    )


@pytest.mark.parametrize("category", ["user", "feedback", "project", "reference"])
def test_parse_drops_meta_paraphrase_lines(category):
    """End-to-end: a category-prefixed meta-paraphrase line must
    return None from parse_extractor_output (= dropped, not stored)."""
    raw = f"{category}: The user is asking about the history of England."
    assert parse_extractor_output(raw) is None


# ── Allow: genuine first-person facts ────────────────────────────────


@pytest.mark.parametrize("content", [
    "Ulrich's wife is named Lizzy.",
    "Coding Kiddos charges $600 for 6 months ($100/mo) per student.",
    "Coding Kiddos curriculum covers Python, JavaScript, and Lua.",
    "Ulrich runs Pretva, a ride-hailing service in Cameroon.",
    "User prefers responses to start with content, not 'Sure thing'.",
    "Bugs are tracked in Linear project INGEST.",
    # Hedged-but-real facts about user projects / entities (passing
    # added 2026-05-08 after code review narrowed _META_PARAPHRASE_RE
    # to start-anchored narration subjects). The broad "X appears to
    # involve Y" rule was rejecting plausible project facts; the
    # few-shot extractor prompt now leans on its anti-examples to
    # discourage hedge-shapes at the LLM level instead.
    "Pretva appears to involve regulatory work in Cameroon.",
    "Coding Kiddos seems to focus on teaching kids Python.",
])
def test_real_facts_pass(content):
    assert not _is_meta_paraphrase(content), (
        f"genuine fact wrongly flagged as meta-paraphrase: {content!r}"
    )


def test_parse_keeps_real_facts():
    raw = "user: Ulrich's wife is named Lizzy."
    parsed = parse_extractor_output(raw)
    assert parsed is not None
    assert parsed.category == "user"
    assert "Lizzy" in parsed.content
