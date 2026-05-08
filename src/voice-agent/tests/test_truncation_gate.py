"""Tests for _truncate_to_heard_portion — the barge-in truncation gate
that rewrites an assistant turn to only the heard portion of audio.

Spec: docs/superpowers/specs/2026-05-07-barge-in-truncation-design.md
"""
from __future__ import annotations
from types import SimpleNamespace

from jarvis_agent import _truncate_to_heard_portion


def _make_item(content):
    return SimpleNamespace(content=content)


class TestTruncationGate:
    def test_empty_table_returns_full_text_no_mutation(self):
        item = _make_item("Hello world.")
        text, mutated = _truncate_to_heard_portion(item, [], audio_end_ms=500)
        assert text == "Hello world."
        assert mutated is False
        assert item.content == "Hello world."

    def test_audio_end_ms_zero_returns_empty(self):
        item = _make_item("Hello world.")
        table = [(100, 6), (200, 12)]
        text, mutated = _truncate_to_heard_portion(item, table, audio_end_ms=0)
        assert text == ""
        assert mutated is True
        assert item.content == ""

    def test_cut_at_chunk_boundary(self):
        # Simulates: 2 synth calls, "Hello sir, " (100ms, 11 chars)
        # then "I'm here." (200ms cumulative, 20 chars cumulative).
        # Interrupt at 100ms exactly — heard only first chunk.
        item = _make_item("Hello sir, I'm here.")
        table = [(100, 11), (200, 20)]
        text, mutated = _truncate_to_heard_portion(item, table, audio_end_ms=100)
        assert text == "Hello sir, "
        assert mutated is True
        assert item.content == "Hello sir, "

    def test_cut_mid_second_chunk_falls_back_to_first_boundary(self):
        # User interrupted partway through chunk 2 — we keep only chunk 1
        # (chunk-boundary cut policy from spec).
        item = _make_item("Hello sir, I'm here.")
        table = [(100, 11), (200, 20)]
        text, mutated = _truncate_to_heard_portion(item, table, audio_end_ms=150)
        assert text == "Hello sir, "
        assert mutated is True

    def test_audio_end_ms_past_end_returns_full_no_mutation(self):
        # False/late interrupt — user heard everything.
        item = _make_item("Hello world.")
        table = [(100, 6), (200, 12)]
        text, mutated = _truncate_to_heard_portion(item, table, audio_end_ms=500)
        assert text == "Hello world."
        assert mutated is False
        assert item.content == "Hello world."

    def test_cut_chars_exceeds_text_length_no_mutation(self):
        # Defensive: position table claims more chars than item.content has
        # (could happen if sanitizers shortened text post-synthesis). Don't
        # crash, don't mutate.
        item = _make_item("Hi.")
        table = [(100, 99)]
        text, mutated = _truncate_to_heard_portion(item, table, audio_end_ms=100)
        assert text == "Hi."
        assert mutated is False

    def test_mutation_when_content_is_list(self):
        # livekit-agents wraps content in a list of strings sometimes;
        # the helper must handle both shapes.
        item = _make_item(["Hello world."])
        table = [(100, 5)]
        text, mutated = _truncate_to_heard_portion(item, table, audio_end_ms=100)
        assert text == "Hello"
        assert mutated is True
        assert item.content == ["Hello"]

    def test_none_content_returns_empty_no_mutation(self):
        item = _make_item(None)
        text, mutated = _truncate_to_heard_portion(item, [(100, 5)], audio_end_ms=100)
        assert text == ""
        assert mutated is False
