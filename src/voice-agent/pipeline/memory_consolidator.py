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

import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

# Ensure src/hub is importable for the lazy `from client import HubClient`
# call in `_read_memories_for_category` — same pattern tools/memory.py uses.
_HUB_DIR = str(Path(__file__).parent.parent / "hub")
if _HUB_DIR not in sys.path:
    sys.path.insert(0, _HUB_DIR)

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
