# Memory Consolidator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a threshold-triggered, LLM-driven consolidation pass that dedupes and merges near-duplicate memories in `~/.jarvis/hub/state.db::memories`, sibling to `pipeline/memory_extractor.py`.

**Architecture:** New module `pipeline/memory_consolidator.py` exposing `record_extraction()` (called from the extractor on success) which trips a counter; on every Nth call (default 10) it fires `consolidate_all_categories()` via `asyncio.create_task`. That fans out to per-category LLM calls (`user`, `feedback`, `project`, `reference`), strict validates the JSON output, then applies clusters via the existing `_publish_event_async("memory.value.{upserted,removed}")` path. All writes are atomic per-cluster; failures degrade to no-op for the affected scope.

**Tech Stack:** Python 3.13, asyncio, Groq llama-3.1-8b-instant via httpx, sqlite3 read via `hub.client.HubClient.read_memories_sync`, pytest + monkeypatch.

**Spec:** [docs/superpowers/specs/2026-05-08-memory-consolidator-design.md](../specs/2026-05-08-memory-consolidator-design.md)

---

## File structure

| Path | Action | Responsibility |
|:--|:--|:--|
| `src/voice-agent/pipeline/memory_consolidator.py` | Create | Counter + threshold trigger, fanout, parser, validator, LLM seam, apply |
| `src/voice-agent/tests/test_memory_consolidator_2026_05_08.py` | Create | 14 tests covering parser + applier + counter + concurrency + env gate |
| `src/voice-agent/pipeline/memory_extractor.py` | Modify | One-line addition: `record_extraction()` after `_mark_extraction_success()` |
| `CLAUDE.md` | Modify | Add a 4-line bullet to "Active design decisions" documenting the module + 3 env vars |

---

## Task 1: Pure parser + validator (`parse_consolidator_output`)

**Files:**
- Create: `src/voice-agent/pipeline/memory_consolidator.py`
- Create: `src/voice-agent/tests/test_memory_consolidator_2026_05_08.py`

This task lays the module skeleton + ships the pure parser. The parser is a pure function — no DB, no LLM, no globals. We TDD it to lock down the validation rules before adding side effects.

- [ ] **Step 1: Write the failing tests for parser**

Create `src/voice-agent/tests/test_memory_consolidator_2026_05_08.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_memory_consolidator_2026_05_08.py -x --tb=short -q
```

Expected: `ModuleNotFoundError: No module named 'pipeline.memory_consolidator'` on every test (8 errors).

- [ ] **Step 3: Create the module skeleton + parser**

Create `src/voice-agent/pipeline/memory_consolidator.py`:

```python
# src/voice-agent/pipeline/memory_consolidator.py
"""Memory consolidator — dedupes and merges near-duplicate entries
in state.db::memories. Sibling to memory_extractor.py.

Trigger: after every Nth successful per-turn extraction
(see `record_extraction()`). Per-category LLM call against
llama-3.1-8b-instant; clusters of 2+ semantically-equivalent entries
are replaced by ONE canonical merged content.

Design spec: docs/superpowers/specs/2026-05-08-memory-consolidator-design.md

Safety:
- Memories younger than _YOUNG_EXCLUSION_SECONDS are excluded from
  candidates (active-conversation extractions can't get merged mid-flow).
- All LLM-output validation runs through `parse_consolidator_output`
  before any write hits the publish path.
- Single-event-loop concurrency guard prevents two simultaneous runs.
- Disabled with JARVIS_MEMORY_CONSOLIDATOR=0.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

# Re-use the meta-paraphrase reject filter from the extractor so a
# canonical that drifts into narration shape ("The user is X-ing")
# is dropped by the same regex that gates per-turn extractions.
from pipeline.memory_extractor import _META_PARAPHRASE_RE

logger = logging.getLogger("jarvis.memory_consolidator")

_VALID_CATEGORIES = ("user", "feedback", "project", "reference")
_MAX_CONTENT_CHARS = 500


@dataclass(frozen=True)
class Cluster:
    members: list[str]      # memory_ids being merged
    canonical: str          # merged content
    category: str           # one of _VALID_CATEGORIES


def parse_consolidator_output(
    raw: str | None,
    valid_ids: set[str],
    category: str,
) -> list[Cluster]:
    """Parse the LLM's JSON output into validated Cluster objects.

    Pure function. No DB, no I/O. Returns [] for any input that fails
    validation (cheaper than raising; calling code treats [] as 'no
    work to do this round').

    Validation rules (must ALL pass per cluster):
    - JSON-parseable with shape {"clusters": [{"members": [...],
      "canonical": "..."}, ...]}
    - Every member ID is in valid_ids (LLM didn't hallucinate)
    - members has length >= 2 (singletons are no-ops)
    - canonical is a non-empty string
    - canonical is <= _MAX_CONTENT_CHARS
    - canonical does NOT match _META_PARAPHRASE_RE
    """
    if not raw or not isinstance(raw, str):
        return []
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []
    clusters_raw = obj.get("clusters") if isinstance(obj, dict) else None
    if not isinstance(clusters_raw, list):
        return []

    out: list[Cluster] = []
    for c in clusters_raw:
        if not isinstance(c, dict):
            continue
        members = c.get("members")
        canonical = c.get("canonical")
        if not isinstance(members, list) or len(members) < 2:
            continue
        if not all(isinstance(m, str) for m in members):
            continue
        if any(m not in valid_ids for m in members):
            continue
        if not isinstance(canonical, str):
            continue
        canonical = canonical.strip()
        if not canonical or len(canonical) > _MAX_CONTENT_CHARS:
            continue
        if _META_PARAPHRASE_RE.search(canonical):
            continue
        out.append(Cluster(members=list(members), canonical=canonical, category=category))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_memory_consolidator_2026_05_08.py -x --tb=short -q
```

Expected: `8 passed`.

- [ ] **Step 5: Commit**

```bash
cd /home/ulrich/Documents/Projects/jarvis
git add src/voice-agent/pipeline/memory_consolidator.py \
        src/voice-agent/tests/test_memory_consolidator_2026_05_08.py
git commit -m "feat(memory-consolidator): add pure parse_consolidator_output + 8 tests"
```

---

## Task 2: `_filter_young_memories` (drop in-flight extractions)

**Files:**
- Modify: `src/voice-agent/pipeline/memory_consolidator.py`
- Modify: `src/voice-agent/tests/test_memory_consolidator_2026_05_08.py`

Memories younger than 5 minutes are excluded from candidates so an active conversation's just-extracted facts can't be merged mid-flow.

- [ ] **Step 1: Append the failing tests**

Append to `src/voice-agent/tests/test_memory_consolidator_2026_05_08.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_memory_consolidator_2026_05_08.py -x --tb=short -q
```

Expected: `ImportError: cannot import name '_filter_young_memories'` (3 errors).

- [ ] **Step 3: Add `_filter_young_memories` to the module**

Append to `src/voice-agent/pipeline/memory_consolidator.py`:

```python
import time


def _filter_young_memories(
    rows: list[dict],
    exclusion_seconds: int,
    now_ms: int | None = None,
) -> list[dict]:
    """Drop rows whose `created_ts` (Unix ms) is younger than
    `exclusion_seconds`. Rows missing `created_ts` are dropped too —
    we can't risk merging entries we can't time.

    `now_ms` is injected for tests; defaults to wall clock."""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    cutoff_ms = now_ms - (exclusion_seconds * 1000)
    return [r for r in rows if isinstance(r.get("created_ts"), int) and r["created_ts"] <= cutoff_ms]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_memory_consolidator_2026_05_08.py -x --tb=short -q
```

Expected: `11 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/pipeline/memory_consolidator.py \
        src/voice-agent/tests/test_memory_consolidator_2026_05_08.py
git commit -m "feat(memory-consolidator): add _filter_young_memories with 3 tests"
```

---

## Task 3: `consolidate_category` (apply a list of clusters via injected publisher)

**Files:**
- Modify: `src/voice-agent/pipeline/memory_consolidator.py`
- Modify: `src/voice-agent/tests/test_memory_consolidator_2026_05_08.py`

Single-category orchestrator. Takes already-validated clusters + a publisher seam (`async (event_type: str, payload: dict) -> None`) so tests don't depend on the hub. Order is: upsert canonical, then remove members. On any publisher exception, abort the rest of the clusters in this call.

- [ ] **Step 1: Append the failing tests**

Append to `src/voice-agent/tests/test_memory_consolidator_2026_05_08.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: `ImportError: cannot import name '_apply_clusters'` (3 errors).

- [ ] **Step 3: Implement `_apply_clusters` + `Publisher` type**

Append to `src/voice-agent/pipeline/memory_consolidator.py`:

```python
from typing import Awaitable, Callable

# Publisher seam: production injects tools.memory._publish_event_async;
# tests inject a FakePublisher.
Publisher = Callable[[str, dict], Awaitable[None]]


async def _apply_clusters(clusters: list[Cluster], publisher: Publisher) -> None:
    """Apply each cluster: upsert canonical, then remove each member.
    Aborts the whole call on first publisher exception — leaving any
    partially-applied cluster on disk is acceptable (publish path is
    the source of truth, idempotent on next run)."""
    if not clusters:
        return
    # Lazy import to avoid a hard dep on tools.memory at module load.
    from tools.memory import _memory_id

    for c in clusters:
        new_id = _memory_id(c.canonical)
        try:
            await publisher("memory.value.upserted", {
                "memory_id": new_id,
                "content": c.canonical,
                "category": c.category,
            })
            for old_id in c.members:
                await publisher("memory.value.removed", {"memory_id": old_id})
        except Exception as e:
            logger.warning(
                f"[consolidator] apply aborted mid-cluster: {type(e).__name__}: {e}"
            )
            return
```

- [ ] **Step 4: Run tests to verify they pass**

Expected: `14 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/pipeline/memory_consolidator.py \
        src/voice-agent/tests/test_memory_consolidator_2026_05_08.py
git commit -m "feat(memory-consolidator): add _apply_clusters with publisher seam + 3 tests"
```

---

## Task 4: `consolidate_category` (read → filter → LLM seam → parse → apply)

**Files:**
- Modify: `src/voice-agent/pipeline/memory_consolidator.py`
- Modify: `src/voice-agent/tests/test_memory_consolidator_2026_05_08.py`

Wire it together for one category. The LLM call is a function seam (`llm_fn`) so tests don't hit Groq.

- [ ] **Step 1: Append the failing tests**

Append:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: `ImportError: cannot import name 'consolidate_category'` (5 errors).

- [ ] **Step 3: Implement `consolidate_category`**

Append to the module:

```python
_YOUNG_EXCLUSION_SECONDS_DEFAULT = 300  # 5 min


def _young_exclusion_seconds() -> int:
    """Read at runtime so operator env edits take effect without restart
    (matches the 2026-05-08 specialist-gate runtime-read pattern)."""
    try:
        return int(os.environ.get(
            "JARVIS_MEMORY_CONSOLIDATE_YOUNG_EXCLUSION_S",
            str(_YOUNG_EXCLUSION_SECONDS_DEFAULT),
        ))
    except ValueError:
        return _YOUNG_EXCLUSION_SECONDS_DEFAULT


# LLM seam — tests inject; production uses _call_consolidator_llm
# (added in Task 6). Type: async (category, entries) -> str.
LLMFn = Callable[[str, list[dict]], Awaitable[str]]


async def consolidate_category(
    category: str,
    rows: list[dict],
    publisher: Publisher,
    llm_fn: LLMFn,
) -> None:
    """Read → filter young → LLM → parse → apply. Caller responsibility:
    pass `rows` already filtered to a single category. No-op on any
    failure (validation, LLM error, publisher abort). Logs a one-line
    summary on every call."""
    candidates = _filter_young_memories(rows, _young_exclusion_seconds())
    if len(candidates) < 2:
        logger.info(
            f"[consolidator] category={category} candidates={len(candidates)} "
            f"clusters=0 reason=under_threshold"
        )
        return

    valid_ids = {r["memory_id"] for r in candidates}
    try:
        raw = await llm_fn(category, candidates)
    except Exception as e:
        logger.warning(
            f"[consolidator] category={category} llm_error={type(e).__name__}: {e}"
        )
        return

    clusters = parse_consolidator_output(raw, valid_ids, category)
    if not clusters:
        logger.info(
            f"[consolidator] category={category} candidates={len(candidates)} clusters=0"
        )
        return

    await _apply_clusters(clusters, publisher)
    members_total = sum(len(c.members) for c in clusters)
    logger.info(
        f"[consolidator] category={category} candidates={len(candidates)} "
        f"clusters={len(clusters)} merged_into={len(clusters)} "
        f"removed={members_total}"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Expected: `19 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/pipeline/memory_consolidator.py \
        src/voice-agent/tests/test_memory_consolidator_2026_05_08.py
git commit -m "feat(memory-consolidator): add consolidate_category with 5 tests"
```

---

## Task 5: `consolidate_all_categories` + concurrency guard

**Files:**
- Modify: `src/voice-agent/pipeline/memory_consolidator.py`
- Modify: `src/voice-agent/tests/test_memory_consolidator_2026_05_08.py`

Top-level fanout. Reads from `state.db` (via the existing `HubClient.read_memories_sync`) and runs each of the 4 categories sequentially. A module-global `_CONSOLIDATION_IN_FLIGHT: bool` makes concurrent triggers safe.

- [ ] **Step 1: Append the failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: `AttributeError: module 'pipeline.memory_consolidator' has no attribute 'consolidate_all_categories'` (2 errors).

- [ ] **Step 3: Implement `consolidate_all_categories` + helpers**

Append:

```python
# ── Top-level fanout ─────────────────────────────────────────────────


_CONSOLIDATION_IN_FLIGHT: bool = False


def _read_memories_for_category(category: str) -> list[dict]:
    """Read all memories for a single category. Wraps the hub SDK's
    sync reader so tests can monkeypatch this single function instead
    of the whole HubClient."""
    from client import HubClient
    return HubClient.read_memories_sync(category=category, limit=200)


def _default_publisher() -> Publisher:
    """Production publisher: tools.memory._publish_event_async (the
    async function itself, used as the Publisher callable). Tests
    monkeypatch this whole function to return a FakePublisher."""
    from tools.memory import _publish_event_async
    return _publish_event_async


async def consolidate_all_categories() -> None:
    """Top-level entry. Fans out to each known category sequentially.
    Concurrency-guarded with _CONSOLIDATION_IN_FLIGHT — a second trigger
    while one is in flight is dropped (logged). The dropped run's work
    is implicit: the next trigger sees the un-merged entries."""
    global _CONSOLIDATION_IN_FLIGHT
    if _CONSOLIDATION_IN_FLIGHT:
        logger.info("[consolidator] skipping — already in flight")
        return
    _CONSOLIDATION_IN_FLIGHT = True
    try:
        publisher: Publisher = _default_publisher()
        for category in _VALID_CATEGORIES:
            try:
                rows = _read_memories_for_category(category)
            except Exception as e:
                logger.warning(
                    f"[consolidator] read failed for category={category}: "
                    f"{type(e).__name__}: {e}"
                )
                continue
            await consolidate_category(
                category=category,
                rows=rows,
                publisher=publisher,
                llm_fn=_call_consolidator_llm,
            )
    finally:
        _CONSOLIDATION_IN_FLIGHT = False


# Stub LLM call — real implementation lands in Task 6.
async def _call_consolidator_llm(category: str, entries: list[dict]) -> str:
    """Production LLM seam. Replaced in tests by a fake that returns
    canned JSON. Real Groq call is added in Task 6."""
    return '{"clusters": []}'  # placeholder until Task 6
```

Add `import asyncio` at the top of the module (alongside the existing `import json`).

- [ ] **Step 4: Run tests to verify they pass**

Expected: `21 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/pipeline/memory_consolidator.py \
        src/voice-agent/tests/test_memory_consolidator_2026_05_08.py
git commit -m "feat(memory-consolidator): add consolidate_all_categories + concurrency guard"
```

---

## Task 6: Real LLM call (`_call_consolidator_llm`)

**Files:**
- Modify: `src/voice-agent/pipeline/memory_consolidator.py`

The Groq HTTP call. No new tests — production-only path; the LLM seam is already test-mocked. We mirror the extractor's exact patterns (httpx, 5-second timeout, `temperature=0.0`, `max_tokens=600` for the wider output, `stop` markers).

- [ ] **Step 1: Replace the placeholder `_call_consolidator_llm`**

In `src/voice-agent/pipeline/memory_consolidator.py`, replace the stub:

```python
async def _call_consolidator_llm(category: str, entries: list[dict]) -> str:
    """Stub LLM call — real implementation lands in Task 6."""
    return '{"clusters": []}'  # placeholder until Task 6
```

with:

```python
_CONSOLIDATOR_PROMPT = """You consolidate memory entries.

You will be given a list of {n} entries (each: id + content) all of
category '{category}'. Group entries that state the SAME fact about
the SAME subject into clusters of 2+; produce ONE canonical merged
content per cluster.

Rules:
- Cluster only entries that state the same fact. Different facts
  about the same subject (e.g. wife's name vs wife's profession)
  stay separate.
- Canonical content must be a single first-person, declarative
  sentence (max {max_chars} chars).
- NO narration shapes ("the user appears to…"); NO hedge ("seems
  to be…"). Plain assertions only.
- Output JSON ONLY: {{"clusters": [{{"members": [...ids...],
  "canonical": "..."}}]}}. If nothing to merge, output {{"clusters": []}}.

Examples:

ENTRIES:
- a: My wife's name is Lizzy.
- b: Ulrich's wife is named Lizzy.
- c: Ulrich runs a ride-hailing service in Cameroon.

OUTPUT:
{{"clusters": [{{"members": ["a", "b"], "canonical": "Ulrich's wife is named Lizzy."}}]}}

ENTRIES:
- x: Coding Kiddos teaches Python.
- y: Coding Kiddos teaches JavaScript.

OUTPUT:
{{"clusters": []}}

ENTRIES:
{entries_block}

OUTPUT:"""


async def _call_consolidator_llm(category: str, entries: list[dict]) -> str:
    """Call llama-3.1-8b-instant via Groq with the consolidator prompt.
    Mirrors `pipeline.memory_extractor._call_extractor_llm` shape so the
    failure modes (missing key, timeout, non-2xx) are identical."""
    import httpx

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        logger.debug("[consolidator] GROQ_API_KEY missing — skipping")
        return '{"clusters": []}'

    entries_block = "\n".join(
        f"- {e['memory_id']}: {(e.get('content') or '').replace(chr(10), ' ')[:200]}"
        for e in entries
    )
    prompt = _CONSOLIDATOR_PROMPT.format(
        n=len(entries),
        category=category,
        max_chars=_MAX_CONTENT_CHARS,
        entries_block=entries_block,
    )

    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 600,
                    "temperature": 0.0,
                    "stop": ["\nENTRIES:", "\n\n\n"],
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning(
                f"[consolidator] LLM call failed: {type(e).__name__}: {e}"
            )
            return '{"clusters": []}'
```

- [ ] **Step 2: Run all consolidator tests to verify nothing broke**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/test_memory_consolidator_2026_05_08.py -x --tb=short -q
```

Expected: `21 passed` (same as Task 5; the LLM is reached only via the test seam, not the real path).

- [ ] **Step 3: Commit**

```bash
git add src/voice-agent/pipeline/memory_consolidator.py
git commit -m "feat(memory-consolidator): real Groq llama-3.1-8b-instant call + few-shot prompt"
```

---

## Task 7: `record_extraction` (counter + threshold + scheduler) + env disable

**Files:**
- Modify: `src/voice-agent/pipeline/memory_consolidator.py`
- Modify: `src/voice-agent/tests/test_memory_consolidator_2026_05_08.py`

The extractor's hook into the consolidator. Increments a counter; on every Nth call, schedules `consolidate_all_categories` via `asyncio.create_task` (fire-and-forget). The `JARVIS_MEMORY_CONSOLIDATOR=0` env var short-circuits.

- [ ] **Step 1: Append the failing tests**

```python
# ── record_extraction (trigger) ──────────────────────────────────────


def test_record_extraction_increments_until_threshold(monkeypatch):
    """At threshold, returns True (would schedule). Counter resets."""
    import pipeline.memory_consolidator as mc
    monkeypatch.setenv("JARVIS_MEMORY_CONSOLIDATOR", "1")
    monkeypatch.setenv("JARVIS_MEMORY_CONSOLIDATE_EVERY_N", "3")
    mc._EXTRACTIONS_SINCE_LAST_CONSOLIDATE = 0
    assert mc.record_extraction(schedule=False) is False
    assert mc.record_extraction(schedule=False) is False
    assert mc.record_extraction(schedule=False) is True   # 3rd → triggers
    assert mc._EXTRACTIONS_SINCE_LAST_CONSOLIDATE == 0    # reset
    assert mc.record_extraction(schedule=False) is False  # next cycle


def test_record_extraction_disabled_when_env_zero(monkeypatch):
    """JARVIS_MEMORY_CONSOLIDATOR=0 makes record_extraction a no-op."""
    import pipeline.memory_consolidator as mc
    monkeypatch.setenv("JARVIS_MEMORY_CONSOLIDATOR", "0")
    monkeypatch.setenv("JARVIS_MEMORY_CONSOLIDATE_EVERY_N", "1")
    mc._EXTRACTIONS_SINCE_LAST_CONSOLIDATE = 0
    # Even at 'every 1' the disable flag wins.
    for _ in range(5):
        assert mc.record_extraction(schedule=False) is False
    assert mc._EXTRACTIONS_SINCE_LAST_CONSOLIDATE == 0


def test_record_extraction_runtime_env_change(monkeypatch):
    """N is read at runtime so operators can adjust without restart
    (mirrors the 2026-05-08 specialist-gate runtime-read pattern)."""
    import pipeline.memory_consolidator as mc
    monkeypatch.setenv("JARVIS_MEMORY_CONSOLIDATOR", "1")
    mc._EXTRACTIONS_SINCE_LAST_CONSOLIDATE = 0
    monkeypatch.setenv("JARVIS_MEMORY_CONSOLIDATE_EVERY_N", "2")
    assert mc.record_extraction(schedule=False) is False
    assert mc.record_extraction(schedule=False) is True  # threshold=2 → trigger
    # Now bump threshold mid-flight; new value must take effect immediately.
    monkeypatch.setenv("JARVIS_MEMORY_CONSOLIDATE_EVERY_N", "3")
    assert mc.record_extraction(schedule=False) is False
    assert mc.record_extraction(schedule=False) is False
    assert mc.record_extraction(schedule=False) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: `AttributeError: module 'pipeline.memory_consolidator' has no attribute 'record_extraction'` (3 errors).

- [ ] **Step 3: Implement `record_extraction`**

Append:

```python
# ── Trigger (called from memory_extractor on each successful extraction) ──


_EXTRACTIONS_SINCE_LAST_CONSOLIDATE: int = 0
_EVERY_N_DEFAULT = 10


def _every_n() -> int:
    """Threshold for triggering. Read at runtime."""
    try:
        n = int(os.environ.get("JARVIS_MEMORY_CONSOLIDATE_EVERY_N", str(_EVERY_N_DEFAULT)))
        return max(1, n)
    except ValueError:
        return _EVERY_N_DEFAULT


def _consolidator_enabled() -> bool:
    return os.environ.get("JARVIS_MEMORY_CONSOLIDATOR", "1") != "0"


def record_extraction(schedule: bool = True) -> bool:
    """Increment the per-extraction counter; if it hits the threshold,
    schedule consolidate_all_categories and reset.

    `schedule=False` is for tests — they want to verify the trigger
    decision without the asyncio.create_task side effect.

    Returns True if a consolidation was triggered (threshold met),
    False otherwise. Always returns False when disabled via env."""
    global _EXTRACTIONS_SINCE_LAST_CONSOLIDATE
    if not _consolidator_enabled():
        return False
    _EXTRACTIONS_SINCE_LAST_CONSOLIDATE += 1
    if _EXTRACTIONS_SINCE_LAST_CONSOLIDATE < _every_n():
        return False
    _EXTRACTIONS_SINCE_LAST_CONSOLIDATE = 0
    if schedule:
        try:
            asyncio.create_task(consolidate_all_categories())
        except RuntimeError:
            # No running loop — can happen if extractor runs outside the
            # voice-agent's main event loop. Fall back to a logged skip;
            # next trigger will re-arm.
            logger.warning("[consolidator] no event loop — skipping schedule")
            return False
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Expected: `24 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/pipeline/memory_consolidator.py \
        src/voice-agent/tests/test_memory_consolidator_2026_05_08.py
git commit -m "feat(memory-consolidator): add record_extraction trigger + env disable"
```

---

## Task 8: Wire trigger into `extract_memory_from_turn` + isolate tests

**Files:**
- Modify: `src/voice-agent/pipeline/memory_extractor.py`
- Modify: `src/voice-agent/tests/conftest.py`

Two changes: (a) the extractor calls `record_extraction()` after a successful extraction; (b) the test conftest defaults `JARVIS_MEMORY_CONSOLIDATOR=0` so the existing extractor tests don't accidentally trip the consolidator counter and schedule background tasks that leak into other tests.

- [ ] **Step 1: Read the current `extract_memory_from_turn` block**

```bash
sed -n '270,295p' src/voice-agent/pipeline/memory_extractor.py
```

You'll see (lines may shift slightly):

```python
async def extract_memory_from_turn(transcript: str) -> ExtractedMemory | None:
    if not transcript or not transcript.strip():
        return None
    raw = await _call_extractor_llm(transcript.strip())
    parsed = parse_extractor_output(raw)
    if parsed is not None:
        logger.info(
            f"[extractor] {parsed.category}: {parsed.content[:80]!r}"
        )
        # Mark extractor-success evidence for the confab detector.
        _mark_extraction_success()
    return parsed
```

- [ ] **Step 2: Apply the edit**

Use Edit to change the `if parsed is not None:` block to:

```python
    if parsed is not None:
        logger.info(
            f"[extractor] {parsed.category}: {parsed.content[:80]!r}"
        )
        # Mark extractor-success evidence for the confab detector.
        _mark_extraction_success()
        # Tell the consolidator a successful extraction landed; on every
        # Nth call (default 10) it schedules consolidate_all_categories
        # via asyncio.create_task. Lazy import so a circular at module
        # load doesn't surface (consolidator imports _META_PARAPHRASE_RE
        # from this module).
        try:
            from pipeline.memory_consolidator import record_extraction
            record_extraction()
        except Exception as e:
            logger.warning(
                f"[extractor] record_extraction failed: {type(e).__name__}: {e}"
            )
```

- [ ] **Step 3: Update `tests/conftest.py` to disable consolidator by default**

Add the consolidator-disable env var to the existing `pytest_configure` block in `src/voice-agent/tests/conftest.py`. Find this block:

```python
def pytest_configure(config) -> None:
    for name in (
        "SUMMARIZE",
        "WEATHER",
        "RESEARCHER",
        "VALIDATOR",
        "CODE_REVIEWER",
        "MEMORY_RECALL",
        "GITHUB",
    ):
        os.environ.setdefault(f"JARVIS_SUBAGENT_{name}", "1")
```

Append after the for-loop, still inside `pytest_configure`:

```python
    # Memory consolidator: default OFF in tests so existing extractor
    # tests (test_extractor_marks_success_on_parse etc.) don't trip
    # the trigger counter and schedule background asyncio tasks that
    # leak across tests. Tests that specifically validate the
    # consolidator monkeypatch.setenv("JARVIS_MEMORY_CONSOLIDATOR", "1")
    # in their own scope.
    os.environ.setdefault("JARVIS_MEMORY_CONSOLIDATOR", "0")
```

Also update the consolidator tests (`test_record_extraction_increments_until_threshold` and the runtime-env-change test) to explicitly `monkeypatch.setenv("JARVIS_MEMORY_CONSOLIDATOR", "1")` — they already do (in Task 7's tests).

- [ ] **Step 4: Run the FULL voice-agent suite to verify nothing regressed**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/ --tb=short -q
```

Expected: existing tests stay green; consolidator tests still pass.

- [ ] **Step 5: Commit**

```bash
git add src/voice-agent/pipeline/memory_extractor.py src/voice-agent/tests/conftest.py
git commit -m "feat(memory-extractor): notify consolidator + isolate test env"
```

---

## Task 9: Document in CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

One bullet under "Active design decisions" so future sessions know the consolidator exists and how to disable it.

- [ ] **Step 1: Add the bullet**

Find this line in `CLAUDE.md`:

```markdown
- **LangGraph supervisor** (`JARVIS_LANGGRAPH_SUPERVISOR=1`, default off) is gated by [supervisor_graph/llm_adapter.py](src/voice-agent/supervisor_graph/llm_adapter.py).
```

Append the following bullet immediately after the LangGraph bullet:

```markdown

- **Memory consolidator** (added 2026-05-08, default ON, kill: `JARVIS_MEMORY_CONSOLIDATOR=0`). [pipeline/memory_consolidator.py](src/voice-agent/pipeline/memory_consolidator.py) runs after every `JARVIS_MEMORY_CONSOLIDATE_EVERY_N` (default 10) successful per-turn extractions. Per-category LLM call (llama-3.1-8b-instant) returns clusters of near-duplicate memories; canonical content replaces them via the existing `_publish_event_async("memory.value.{upserted,removed}")` path. Memories younger than `JARVIS_MEMORY_CONSOLIDATE_YOUNG_EXCLUSION_S` (default 300 s) are excluded so active-conversation extractions don't get merged mid-flow. Single-event-loop concurrency guard. All env vars read at runtime.
```

- [ ] **Step 2: Verify no broken markdown**

```bash
head -100 CLAUDE.md | grep -nE "^- \*\*Memory consolidator" && \
  echo "OK: bullet present"
```

Expected: one match line printed, then "OK: bullet present".

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): document memory consolidator + 3 env vars"
```

---

## Task 10: Final full-suite verification

**Files:** none modified. Just run the suite.

- [ ] **Step 1: Run the full voice-agent suite**

```bash
cd src/voice-agent && .venv/bin/python -m pytest tests/ -q
```

Expected: all previous tests still pass + 24 new consolidator tests pass. Total should be `1057+ passed` (was 1033 before this plan; +24 from this plan; minor variation if other in-flight changes landed).

- [ ] **Step 2: Push the branch**

```bash
cd /home/ulrich/Documents/Projects/jarvis && git push 2>&1 | tail -5
```

- [ ] **Step 3: Final smoke — telemetry-safe restart of voice-agent**

```bash
LAST_TS=$(sqlite3 ~/.local/share/jarvis/turn_telemetry.db \
  "SELECT CAST((strftime('%s','now') - strftime('%s', ts_utc)) AS INTEGER) FROM turns ORDER BY ts_utc DESC LIMIT 1")
if [ "$LAST_TS" -ge 60 ] || [ -z "$LAST_TS" ]; then
  systemctl --user restart jarvis-voice-agent.service
  sleep 3
  systemctl --user is-active jarvis-voice-agent.service
else
  echo "ABORT: voice-agent had a turn ${LAST_TS}s ago — ASK USER before restart"
fi
```

Expected: `active`, or the ABORT branch if a session is in flight (in which case stop here and ask the user before restarting).

---

## What we built

| Surface | Output |
|:--|:--|
| New module | `pipeline/memory_consolidator.py` (~250 lines) |
| New tests | `tests/test_memory_consolidator_2026_05_08.py` (24 tests) |
| Extractor hook | one ~10-line block added to `extract_memory_from_turn` |
| Doc update | one bullet in `CLAUDE.md` |
| Env vars | `JARVIS_MEMORY_CONSOLIDATOR` (kill), `JARVIS_MEMORY_CONSOLIDATE_EVERY_N` (=10), `JARVIS_MEMORY_CONSOLIDATE_YOUNG_EXCLUSION_S` (=300) |
| Behavior | After every 10 user-turn extractions, scan each of the 4 known categories, ask llama-3.1-8b-instant to identify dup/related clusters, write canonical merged entry + remove cluster members. All failures degrade to no-op. |
