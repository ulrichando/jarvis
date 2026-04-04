"""Find relevant memories for a given context."""

from __future__ import annotations

from typing import Optional

from .memoryScan import scan_memories
from .memoryTypes import MemoryEntry


def find_relevant_memories(
    query: str,
    max_results: int = 10,
    config_dir: Optional[str] = None,
) -> list[MemoryEntry]:
    """Find memories relevant to the query, ranked by relevance."""
    results = scan_memories(query, config_dir)
    return sorted(results, key=lambda e: e.relevance_score, reverse=True)[:max_results]
