"""Memory directory management -- read, write, and scan memory files."""

from __future__ import annotations

import os
from typing import Optional

from .memoryTypes import MemoryEntry
from .paths import get_memory_dir, get_memory_file_path


def read_memory(memory_id: str, config_dir: Optional[str] = None) -> Optional[MemoryEntry]:
    """Read a memory entry from disk."""
    path = get_memory_file_path(memory_id, config_dir)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            content = f.read()
        return MemoryEntry(id=memory_id, content=content, path=path)
    except Exception:
        return None


def write_memory(entry: MemoryEntry, config_dir: Optional[str] = None) -> bool:
    """Write a memory entry to disk."""
    path = get_memory_file_path(entry.id, config_dir)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(entry.content)
        return True
    except Exception:
        return False


def list_memories(config_dir: Optional[str] = None) -> list[str]:
    """List all memory IDs in the memory directory."""
    mem_dir = get_memory_dir(config_dir)
    if not os.path.isdir(mem_dir):
        return []
    return [f[:-3] for f in os.listdir(mem_dir) if f.endswith(".md")]
