"""Memory consolidator tests (fix from 2026-05-08 audit follow-up).

Sibling to test_extractor_meta_paraphrase / test_confab_extractor_evidence.
Pure-function tests for parse_consolidator_output + apply path.
No Groq, no DB, no event loop — everything is in-memory.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("GROQ_API_KEY", "test-key-for-init")


# ── Parser / validator ───────────────────────────────────────────────


def test_parse_valid_clusters():
    from pipeline.memory_consolidator import parse_consolidator_output
    raw = (
        '{"clusters": [{"members": ["a", "b"], '
        '"canonical": "Ulrich is married to Lizzy."}]}'
    )
    valid_ids = {"a", "b", "c"}
    clusters = parse_consolidator_output(raw, valid_ids, category="user")
    assert len(clusters) == 1
    c = clusters[0]
    assert c.members == ["a", "b"]
    assert c.canonical == "Ulrich is married to Lizzy."
    assert c.category == "user"


def test_parse_rejects_unknown_member_id():
    from pipeline.memory_consolidator import parse_consolidator_output
    raw = '{"clusters": [{"members": ["a", "ZZZ"], "canonical": "x"}]}'
    clusters = parse_consolidator_output(raw, {"a", "b"}, category="user")
    assert clusters == []


def test_parse_rejects_meta_paraphrase_canonical():
    from pipeline.memory_consolidator import parse_consolidator_output
    raw = (
        '{"clusters": [{"members": ["a", "b"], '
        '"canonical": "The user is asking about wife."}]}'
    )
    clusters = parse_consolidator_output(raw, {"a", "b"}, category="user")
    assert clusters == []


def test_parse_rejects_oversize_canonical():
    from pipeline.memory_consolidator import parse_consolidator_output
    big = "x" * 600
    raw = f'{{"clusters": [{{"members": ["a", "b"], "canonical": "{big}"}}]}}'
    clusters = parse_consolidator_output(raw, {"a", "b"}, category="user")
    assert clusters == []


def test_parse_rejects_singleton_cluster():
    from pipeline.memory_consolidator import parse_consolidator_output
    raw = '{"clusters": [{"members": ["a"], "canonical": "x"}]}'
    clusters = parse_consolidator_output(raw, {"a"}, category="user")
    assert clusters == []


def test_parse_rejects_garbage_input():
    from pipeline.memory_consolidator import parse_consolidator_output
    for raw in ["", "not-json", "{}", '{"clusters": "bad"}', None]:
        assert parse_consolidator_output(raw, {"a", "b"}, "user") == []


def test_parse_rejects_empty_canonical():
    from pipeline.memory_consolidator import parse_consolidator_output
    raw = '{"clusters": [{"members": ["a", "b"], "canonical": ""}]}'
    clusters = parse_consolidator_output(raw, {"a", "b"}, category="user")
    assert clusters == []


def test_parse_no_clusters_returns_empty():
    """LLM saying 'nothing to merge' is the success case for already-clean
    stores — should return [] cleanly, not raise."""
    from pipeline.memory_consolidator import parse_consolidator_output
    assert parse_consolidator_output('{"clusters": []}', {"a"}, "user") == []
