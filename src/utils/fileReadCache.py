"""
In-memory file content cache with automatic invalidation based on modification time.

Eliminates redundant file reads in edit operations.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class CachedFileData:
    """Cached file content with metadata."""

    content: str
    encoding: str
    mtime: float


class FileReadCache:
    """
    A simple in-memory cache for file contents with automatic invalidation
    based on modification time.
    """

    def __init__(self, max_cache_size: int = 1000) -> None:
        self._cache: dict[str, CachedFileData] = {}
        self._max_cache_size = max_cache_size

    def read_file(self, file_path: str) -> dict:
        """
        Read a file with caching. Returns dict with 'content' and 'encoding'.
        Cache is invalidated when the file's modification time changes.
        """
        # Get file stats for cache invalidation
        try:
            stat = os.stat(file_path)
        except OSError:
            self._cache.pop(file_path, None)
            raise

        cached = self._cache.get(file_path)
        if cached is not None and cached.mtime == stat.st_mtime:
            return {"content": cached.content, "encoding": cached.encoding}

        # Cache miss or stale data - read the file
        encoding = "utf-8"  # Default; callers can override
        try:
            with open(file_path, "r", encoding=encoding) as f:
                content = f.read()
        except UnicodeDecodeError:
            encoding = "latin-1"
            with open(file_path, "r", encoding=encoding) as f:
                content = f.read()

        # Normalize CRLF
        content = content.replace("\r\n", "\n")

        # Update cache
        self._cache[file_path] = CachedFileData(
            content=content,
            encoding=encoding,
            mtime=stat.st_mtime,
        )

        # Evict oldest entries if cache is too large
        if len(self._cache) > self._max_cache_size:
            oldest_key = next(iter(self._cache))
            del self._cache[oldest_key]

        return {"content": content, "encoding": encoding}

    def clear(self) -> None:
        """Clear the entire cache."""
        self._cache.clear()

    def invalidate(self, file_path: str) -> None:
        """Remove a specific file from the cache."""
        self._cache.pop(file_path, None)

    def get_stats(self) -> dict:
        """Get cache statistics for debugging/monitoring."""
        return {
            "size": len(self._cache),
            "entries": list(self._cache.keys()),
        }


# Singleton instance
file_read_cache = FileReadCache()
