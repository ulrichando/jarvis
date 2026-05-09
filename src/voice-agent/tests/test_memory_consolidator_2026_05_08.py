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


# ── Young-memory exclusion ───────────────────────────────────────────


def test_filter_young_memories_excludes_recent():
    from pipeline.memory_consolidator import _filter_young_memories
    now_ms = int(time.time() * 1000)
    rows = [
        {"memory_id": "old", "created_ts": now_ms - 10 * 60 * 1000},  # 10 min
        {"memory_id": "new", "created_ts": now_ms - 60 * 1000},       # 60 s
    ]
    kept = _filter_young_memories(rows, exclusion_seconds=300, now_ms=now_ms)
    assert [r["memory_id"] for r in kept] == ["old"]


def test_filter_young_memories_keeps_all_when_old_enough():
    from pipeline.memory_consolidator import _filter_young_memories
    now_ms = int(time.time() * 1000)
    rows = [
        {"memory_id": "a", "created_ts": now_ms - 600 * 1000},
        {"memory_id": "b", "created_ts": now_ms - 1200 * 1000},
    ]
    kept = _filter_young_memories(rows, exclusion_seconds=300, now_ms=now_ms)
    assert len(kept) == 2


def test_filter_young_memories_handles_missing_created_ts():
    """Defensive: a row missing created_ts is treated as 'unknown age'
    and excluded (we don't risk merging something we can't time)."""
    from pipeline.memory_consolidator import _filter_young_memories
    now_ms = int(time.time() * 1000)
    rows = [{"memory_id": "x"}]
    kept = _filter_young_memories(rows, exclusion_seconds=300, now_ms=now_ms)
    assert kept == []
