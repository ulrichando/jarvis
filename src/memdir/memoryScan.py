"""Memory scanning -- scan for relevant memories."""

from __future__ import annotations

from typing import Optional

from .memdir import list_memories, read_memory
from .memoryTypes import MemoryEntry


def scan_memories(query: str, config_dir: Optional[str] = None) -> list[MemoryEntry]:
    """Scan memories for entries relevant to the query."""
    results = []
    for mid in list_memories(config_dir):
        entry = read_memory(mid, config_dir)
        if entry and query.lower() in entry.content.lower():
            results.append(entry)
    return results
