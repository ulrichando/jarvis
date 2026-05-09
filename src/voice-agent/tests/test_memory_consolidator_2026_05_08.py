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


# ── Apply (publisher orchestration) ──────────────────────────────────


class _FakePublisher:
    """Captures ('upserted'|'removed', payload) tuples in order."""
    def __init__(self, fail_on_call: int | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.fail_on_call = fail_on_call

    async def __call__(self, event_type: str, payload: dict) -> None:
        if self.fail_on_call is not None and len(self.calls) == self.fail_on_call:
            raise RuntimeError("simulated publish failure")
        # event_type looks like "memory.value.upserted"; reduce to last token
        kind = event_type.rsplit(".", 1)[-1]
        self.calls.append((kind, payload))


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_apply_clusters_publishes_upsert_then_remove():
    from pipeline.memory_consolidator import Cluster, _apply_clusters
    pub = _FakePublisher()
    clusters = [Cluster(members=["a", "b"], canonical="merged.", category="user")]
    _run(_apply_clusters(clusters, publisher=pub))
    # Expect: 1 upsert + 2 removes, in that order.
    assert [c[0] for c in pub.calls] == ["upserted", "removed", "removed"]
    upsert_payload = pub.calls[0][1]
    assert upsert_payload["content"] == "merged."
    assert upsert_payload["category"] == "user"
    assert "memory_id" in upsert_payload
    removed_ids = sorted(c[1]["memory_id"] for c in pub.calls[1:])
    assert removed_ids == ["a", "b"]


def test_apply_aborts_on_publish_exception():
    from pipeline.memory_consolidator import Cluster, _apply_clusters
    pub = _FakePublisher(fail_on_call=1)  # fail on the first remove
    clusters = [
        Cluster(members=["a", "b"], canonical="x.", category="user"),
        Cluster(members=["c", "d"], canonical="y.", category="user"),
    ]
    _run(_apply_clusters(clusters, publisher=pub))
    # First cluster: upsert succeeded (call 0), first remove failed (call 1).
    # Second cluster: never attempted — abort-on-exception is bounded to
    # 1 already-published canonical (acceptable, documented in spec).
    kinds = [c[0] for c in pub.calls]
    assert kinds == ["upserted"]


def test_apply_empty_clusters_is_noop():
    from pipeline.memory_consolidator import _apply_clusters
    pub = _FakePublisher()
    _run(_apply_clusters([], publisher=pub))
    assert pub.calls == []


# ── consolidate_category (single category orchestration) ─────────────


def test_consolidate_category_skips_when_under_two_entries():
    from pipeline.memory_consolidator import consolidate_category
    pub = _FakePublisher()

    async def fake_llm(category, entries):
        raise AssertionError("LLM should not be called for <2 entries")

    rows = [{"memory_id": "a", "content": "only one.",
             "created_ts": int(time.time()*1000) - 600_000}]
    _run(consolidate_category("user", rows, publisher=pub, llm_fn=fake_llm))
    assert pub.calls == []


def test_consolidate_category_skips_when_all_young():
    from pipeline.memory_consolidator import consolidate_category
    pub = _FakePublisher()

    async def fake_llm(category, entries):
        raise AssertionError("LLM should not be called when all young")

    now_ms = int(time.time() * 1000)
    rows = [
        {"memory_id": "a", "content": "x.", "created_ts": now_ms - 30_000},
        {"memory_id": "b", "content": "y.", "created_ts": now_ms - 60_000},
    ]
    _run(consolidate_category("user", rows, publisher=pub, llm_fn=fake_llm))
    assert pub.calls == []


def test_consolidate_category_happy_path():
    from pipeline.memory_consolidator import consolidate_category
    pub = _FakePublisher()
    now_ms = int(time.time() * 1000)
    rows = [
        {"memory_id": "a", "content": "Ulrich is married to Lizzy.",
         "created_ts": now_ms - 600_000},
        {"memory_id": "b", "content": "Ulrich's wife is named Lizzy.",
         "created_ts": now_ms - 700_000},
    ]

    async def fake_llm(category, entries):
        # Sanity: entries list passed in is the filtered set.
        assert {e["memory_id"] for e in entries} == {"a", "b"}
        return ('{"clusters": [{"members": ["a", "b"], '
                '"canonical": "Ulrich is married to Lizzy."}]}')

    _run(consolidate_category("user", rows, publisher=pub, llm_fn=fake_llm))
    kinds = [c[0] for c in pub.calls]
    assert kinds == ["upserted", "removed", "removed"]


def test_consolidate_category_llm_garbage_is_noop():
    from pipeline.memory_consolidator import consolidate_category
    pub = _FakePublisher()
    now_ms = int(time.time() * 1000)
    rows = [
        {"memory_id": "a", "content": "x.", "created_ts": now_ms - 600_000},
        {"memory_id": "b", "content": "y.", "created_ts": now_ms - 700_000},
    ]

    async def fake_llm(category, entries):
        return "not-json {{"

    _run(consolidate_category("user", rows, publisher=pub, llm_fn=fake_llm))
    assert pub.calls == []


def test_consolidate_category_idempotent():
    """Run twice with the same input — second run sees the canonical and
    nothing else, so the LLM (still mocked) returns no clusters."""
    from pipeline.memory_consolidator import consolidate_category
    pub = _FakePublisher()
    now_ms = int(time.time() * 1000)
    rows_first = [
        {"memory_id": "a", "content": "x.", "created_ts": now_ms - 600_000},
        {"memory_id": "b", "content": "y.", "created_ts": now_ms - 700_000},
    ]
    canonical_id = None

    async def first_llm(category, entries):
        return ('{"clusters": [{"members": ["a", "b"], "canonical": "merged."}]}')

    _run(consolidate_category("user", rows_first, publisher=pub, llm_fn=first_llm))
    canonical_id = pub.calls[0][1]["memory_id"]

    # Second run: only the canonical remains.
    pub2 = _FakePublisher()
    rows_second = [{"memory_id": canonical_id, "content": "merged.",
                    "created_ts": now_ms - 800_000}]

    async def second_llm(category, entries):
        return '{"clusters": []}'  # nothing to merge

    _run(consolidate_category("user", rows_second, publisher=pub2, llm_fn=second_llm))
    assert pub2.calls == []


# ── consolidate_all_categories (fanout + concurrency guard) ──────────


def test_consolidate_all_categories_skips_when_in_flight(monkeypatch):
    """A second simultaneous call must early-return; only the first runs."""
    import pipeline.memory_consolidator as mc
    pub = _FakePublisher()
    call_count = {"n": 0}

    def fake_read(category=None, limit=30, db_path=None):
        call_count["n"] += 1
        return []  # empty store; consolidate_category will skip

    monkeypatch.setattr(mc, "_read_memories_for_category", lambda c: fake_read(category=c))
    monkeypatch.setattr(mc, "_default_publisher", lambda: pub)

    async def both():
        # Set the guard manually to simulate "already running".
        mc._CONSOLIDATION_IN_FLIGHT = True
        try:
            await mc.consolidate_all_categories()
        finally:
            mc._CONSOLIDATION_IN_FLIGHT = False

    _run(both())
    # No category was read — the function early-returned on the guard.
    assert call_count["n"] == 0


def test_consolidate_all_categories_runs_each_category(monkeypatch):
    import pipeline.memory_consolidator as mc

    seen_categories: list[str] = []

    def fake_read_for_category(category):
        seen_categories.append(category)
        return []  # empty — consolidate_category skips after read

    async def llm_should_not_be_called(category, entries):
        raise AssertionError("LLM should not be reached when stores are empty")

    monkeypatch.setattr(mc, "_read_memories_for_category", fake_read_for_category)
    monkeypatch.setattr(mc, "_default_publisher", lambda: _FakePublisher())
    monkeypatch.setattr(mc, "_call_consolidator_llm", llm_should_not_be_called)

    _run(mc.consolidate_all_categories())
    assert seen_categories == list(mc._VALID_CATEGORIES)
    assert mc._CONSOLIDATION_IN_FLIGHT is False  # cleared after run
