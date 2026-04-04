"""File indexing utilities for fast file lookup."""

from __future__ import annotations

import os
from typing import Optional


class FileIndex:
    """Index of files in a directory tree for fast lookup."""

    def __init__(self) -> None:
        self._files: dict[str, str] = {}  # basename -> full path

    def build(self, root: str, ignore_patterns: list[str] | None = None) -> None:
        """Build the file index from a directory tree."""
        self._files.clear()
        for dirpath, dirnames, filenames in os.walk(root):
            for fname in filenames:
                full_path = os.path.join(dirpath, fname)
                self._files[fname] = full_path

    def lookup(self, filename: str) -> Optional[str]:
        """Look up a file by basename."""
        return self._files.get(filename)

    def search(self, pattern: str) -> list[str]:
        """Search files matching a pattern."""
        return [p for n, p in self._files.items() if pattern.lower() in n.lower()]

    @property
    def size(self) -> int:
        return len(self._files)
