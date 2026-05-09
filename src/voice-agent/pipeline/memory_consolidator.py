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
import sys
import time
from dataclasses import dataclass
from pathlib import Path

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
