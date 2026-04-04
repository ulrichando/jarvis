"""
File state cache with path normalization and LRU eviction.

Provides a size-limited cache for file contents used by tools that need
to track file state across operations.
"""

from __future__ import annotations

import os
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Iterator, Optional


@dataclass
class FileState:
    """State of a file at a point in time."""

    content: str
    timestamp: float
    offset: Optional[int] = None
    limit: Optional[int] = None
    is_partial_view: bool = False


# Default max entries for read file state caches
READ_FILE_STATE_CACHE_SIZE = 100

# Default size limit (25MB)
DEFAULT_MAX_CACHE_SIZE_BYTES = 25 * 1024 * 1024


class FileStateCache:
    """
    A file state cache that normalizes all path keys before access.

    Ensures consistent cache hits regardless of whether callers pass
    relative vs absolute paths with redundant segments.
    """

    def __init__(self, max_entries: int, max_size_bytes: int) -> None:
        self._cache: OrderedDict[str, FileState] = OrderedDict()
        self._max_entries = max_entries
        self._max_size_bytes = max_size_bytes
        self._current_size_bytes = 0

    @property
    def max(self) -> int:
        return self._max_entries

    @property
    def max_size(self) -> int:
        return self._max_size_bytes

    @property
    def size(self) -> int:
        return len(self._cache)

    @property
    def calculated_size(self) -> int:
        return self._current_size_bytes

    @staticmethod
    def _normalize(key: str) -> str:
        return os.path.normpath(key)

    def _entry_size(self, value: FileState) -> int:
        return max(1, len(value.content.encode("utf-8")))

    def get(self, key: str) -> Optional[FileState]:
        normalized = self._normalize(key)
        value = self._cache.get(normalized)
        if value is not None:
            self._cache.move_to_end(normalized)
        return value

    def set(self, key: str, value: FileState) -> "FileStateCache":
        normalized = self._normalize(key)

        # Remove old entry size if exists
        old = self._cache.get(normalized)
        if old is not None:
            self._current_size_bytes -= self._entry_size(old)

        entry_size = self._entry_size(value)
        self._cache[normalized] = value
        self._cache.move_to_end(normalized)
        self._current_size_bytes += entry_size

        # Evict oldest entries if over limits
        while (
            len(self._cache) > self._max_entries
            or self._current_size_bytes > self._max_size_bytes
        ) and self._cache:
            evicted_key, evicted_val = self._cache.popitem(last=False)
            self._current_size_bytes -= self._entry_size(evicted_val)

        return self

    def has(self, key: str) -> bool:
        return self._normalize(key) in self._cache

    def delete(self, key: str) -> bool:
        normalized = self._normalize(key)
        if normalized in self._cache:
            val = self._cache.pop(normalized)
            self._current_size_bytes -= self._entry_size(val)
            return True
        return False

    def clear(self) -> None:
        self._cache.clear()
        self._current_size_bytes = 0

    def keys(self) -> Iterator[str]:
        return iter(self._cache.keys())

    def entries(self) -> Iterator[tuple[str, FileState]]:
        return iter(self._cache.items())


def create_file_state_cache_with_size_limit(
    max_entries: int,
    max_size_bytes: int = DEFAULT_MAX_CACHE_SIZE_BYTES,
) -> FileStateCache:
    """Factory function to create a size-limited FileStateCache."""
    return FileStateCache(max_entries, max_size_bytes)


def cache_to_object(cache: FileStateCache) -> dict[str, FileState]:
    """Convert cache to a plain dictionary."""
    return dict(cache.entries())


def cache_keys(cache: FileStateCache) -> list[str]:
    """Get all keys from cache."""
    return list(cache.keys())


def clone_file_state_cache(cache: FileStateCache) -> FileStateCache:
    """Clone a FileStateCache, preserving size limit configuration."""
    cloned = create_file_state_cache_with_size_limit(cache.max, cache.max_size)
    for key, value in cache.entries():
        cloned.set(key, value)
    return cloned


def merge_file_state_caches(
    first: FileStateCache, second: FileStateCache
) -> FileStateCache:
    """
    Merge two file state caches.
    More recent entries (by timestamp) override older ones.
    """
    merged = clone_file_state_cache(first)
    for file_path, file_state in second.entries():
        existing = merged.get(file_path)
        if existing is None or file_state.timestamp > existing.timestamp:
            merged.set(file_path, file_state)
    return merged
