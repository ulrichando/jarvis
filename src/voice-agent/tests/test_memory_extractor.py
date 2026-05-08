# src/voice-agent/tests/test_memory_extractor.py
"""Unit tests for the auto-extraction memory pipeline.

The extractor runs on every user turn after STT finalization. It
classifies whether the transcript contains a stable, memorable
fact about the user/their work, and if so emits a category +
content pair for direct write to state.db.memories — bypassing
the supervisor LLM's tool-choice surface entirely.
"""
from __future__ import annotations
import pytest
from pipeline.memory_extractor import (
    parse_extractor_output,
    ExtractedMemory,
    EXTRACTOR_SKIP,
)


def test_parse_skip_returns_none():
    assert parse_extractor_output("SKIP") is None
    assert parse_extractor_output("  SKIP  ") is None
    assert parse_extractor_output("skip") is None


def test_parse_user_category():
    out = parse_extractor_output("user: Ulrich's wife is named Lizzy")
    assert out is not None
    assert out.category == "user"
    assert out.content == "Ulrich's wife is named Lizzy"


def test_parse_project_category():
    out = parse_extractor_output(
        "project: Coding Kiddos charges $600 for 6 months ($100/mo)."
    )
    assert out.category == "project"
    assert "Coding Kiddos" in out.content


def test_parse_invalid_category_returns_none():
    """Defensive: extractor LLM might output a bad category. Drop
    it rather than write garbage."""
    assert parse_extractor_output("nonsense: who knows what this is") is None


def test_parse_handles_unprefixed_text():
    """If the extractor LLM forgets the category prefix, treat as
    SKIP rather than guess."""
    assert parse_extractor_output("Ulrich's wife is named Lizzy") is None


def test_parse_strips_quotes():
    out = parse_extractor_output('user: "Ulrich runs Pretva"')
    assert out.content == "Ulrich runs Pretva"


def test_extracted_memory_max_length():
    """Don't write giant memories — cap at the same 500 char limit
    as remember() in tools/memory.py."""
    long_content = "x" * 600
    out = parse_extractor_output(f"project: {long_content}")
    assert out is None or len(out.content) <= 500


def test_extractor_skip_constant():
    assert EXTRACTOR_SKIP == "SKIP"
