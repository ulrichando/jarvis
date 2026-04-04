"""MemoryDirectory -- persistent key-value memory stored as markdown files
with YAML frontmatter.

Each entry is stored as:
    {base_dir}/{category}/{key}.md
with frontmatter fields: name, description, type, created_at, updated_at, tags.

The MemoryDirectory class provides store/recall/search/list/delete/stats
operations and maintains a MEMORY.md entrypoint index.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config import JARVIS_HOME

log = logging.getLogger("jarvis.memory.memdir")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ENTRYPOINT_NAME = "MEMORY.md"
MAX_ENTRYPOINT_LINES = 200
MAX_ENTRYPOINT_BYTES = 25_000
MAX_MEMORY_FILES = 200

MEMORY_TYPES = ("user", "feedback", "project", "reference")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitize_key(key: str) -> str:
    """Sanitize a key for use as a filename.

    Rejects null bytes, URL-encoded traversals, backslashes, and absolute paths.
    """
    if "\0" in key:
        raise ValueError(f"Null byte in key: {key!r}")
    if "\\" in key:
        raise ValueError(f"Backslash in key: {key!r}")
    if key.startswith("/"):
        raise ValueError(f"Absolute path key: {key!r}")
    if ".." in key:
        raise ValueError(f"Path traversal in key: {key!r}")

    # Lowercase, replace spaces, strip unsafe chars
    safe = key.lower().strip()
    safe = safe.replace(" ", "_")
    safe = re.sub(r"[^a-z0-9_\-]", "", safe)
    return safe[:80] or "untitled"


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse YAML frontmatter from markdown text.

    Returns (metadata_dict, body_text).
    """
    if not text.startswith("---"):
        return {}, text

    end = text.find("---", 3)
    if end == -1:
        return {}, text

    meta: dict[str, str] = {}
    for line in text[3:end].strip().splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()

    body = text[end + 3:].strip()
    return meta, body


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _memory_age_days(mtime: float) -> int:
    """Days elapsed since mtime. Floor-rounded, clamped to >= 0."""
    return max(0, int((time.time() - mtime) // 86400))


def _memory_age_str(mtime: float) -> str:
    """Human-readable age string."""
    d = _memory_age_days(mtime)
    if d == 0:
        return "today"
    if d == 1:
        return "yesterday"
    return f"{d} days ago"


def _format_size(n: int) -> str:
    """Human-readable byte size."""
    if n >= 1_048_576:
        return f"{n / 1_048_576:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def _truncate_entrypoint(raw: str) -> tuple[str, bool]:
    """Truncate entrypoint content to line and byte caps.

    Returns (content, was_truncated).
    """
    trimmed = raw.strip()
    lines = trimmed.split("\n")
    line_count = len(lines)
    byte_count = len(trimmed)

    was_line_truncated = line_count > MAX_ENTRYPOINT_LINES
    was_byte_truncated = byte_count > MAX_ENTRYPOINT_BYTES

    if not was_line_truncated and not was_byte_truncated:
        return trimmed, False

    truncated = "\n".join(lines[:MAX_ENTRYPOINT_LINES]) if was_line_truncated else trimmed

    if len(truncated) > MAX_ENTRYPOINT_BYTES:
        cut_at = truncated.rfind("\n", 0, MAX_ENTRYPOINT_BYTES)
        truncated = truncated[:cut_at if cut_at > 0 else MAX_ENTRYPOINT_BYTES]

    reason_parts = []
    if was_line_truncated:
        reason_parts.append(f"{line_count} lines (limit: {MAX_ENTRYPOINT_LINES})")
    if was_byte_truncated:
        reason_parts.append(f"{_format_size(byte_count)} (limit: {_format_size(MAX_ENTRYPOINT_BYTES)})")

    warning = f"\n\n> WARNING: {ENTRYPOINT_NAME} is {' and '.join(reason_parts)}. Only part of it was loaded."
    return truncated + warning, True


def _word_set(text: str) -> set[str]:
    """Return the set of lowercase alphanumeric words in text."""
    return set(re.findall(r"[a-z0-9]+", text.lower()))


# ---------------------------------------------------------------------------
# MemoryDirectory
# ---------------------------------------------------------------------------


class MemoryDirectory:
    """Persistent key-value memory stored as markdown files with YAML frontmatter.

    Each entry is stored at ``{base_dir}/{category}/{key}.md`` with frontmatter
    containing name, description, type, created_at, updated_at, and tags.

    A ``MEMORY.md`` index file is maintained automatically.
    """

    def __init__(self, base_dir: str = "") -> None:
        if base_dir:
            self._base_dir = Path(base_dir)
        else:
            self._base_dir = JARVIS_HOME / "memory"

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def store(
        self,
        key: str,
        value: str,
        category: str = "general",
        *,
        description: str = "",
        tags: list[str] | None = None,
    ) -> str:
        """Store a memory entry and update the index.

        Parameters
        ----------
        key : str
            Unique key for this memory (used as filename stem).
        value : str
            The memory content (markdown body).
        category : str
            Category subdirectory (e.g. "user", "feedback", "project").
        description : str
            One-line description for the index.
        tags : list[str] | None
            Optional tags for organization.

        Returns
        -------
        str
            Absolute path of the saved file.
        """
        safe_key = _sanitize_key(key)
        safe_cat = _sanitize_key(category)
        cat_dir = self._base_dir / safe_cat
        cat_dir.mkdir(parents=True, exist_ok=True)

        filepath = cat_dir / f"{safe_key}.md"

        # Preserve created_at if updating
        created_at = _now_iso()
        if filepath.exists():
            old_meta, _ = _parse_frontmatter(filepath.read_text(encoding="utf-8"))
            if "created_at" in old_meta:
                created_at = old_meta["created_at"]

        tag_str = ", ".join(tags) if tags else ""
        frontmatter = (
            "---\n"
            f"name: {key}\n"
            f"description: {description or key}\n"
            f"type: {safe_cat}\n"
            f"created_at: {created_at}\n"
            f"updated_at: {_now_iso()}\n"
            f"tags: {tag_str}\n"
            "---\n"
        )
        filepath.write_text(f"{frontmatter}\n{value}\n", encoding="utf-8")
        log.debug("Stored memory: %s -> %s", key, filepath)

        self._update_index()
        return str(filepath)

    def recall(self, key: str) -> str | None:
        """Retrieve a memory by key. Returns the body content or None."""
        safe_key = _sanitize_key(key)

        # Search across all category directories
        for cat_dir in self._iter_category_dirs():
            filepath = cat_dir / f"{safe_key}.md"
            if filepath.exists():
                _, body = _parse_frontmatter(filepath.read_text(encoding="utf-8"))
                return body

        return None

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Search across all entries by keyword matching.

        Returns a list of dicts with keys: name, description, type, content,
        path, age, relevance_score.
        """
        query_words = _word_set(query)
        if not query_words:
            return []

        results: list[dict[str, Any]] = []

        for entry in self._scan_all_entries():
            # Score by word overlap across name + description + content
            entry_words = _word_set(
                f"{entry['name']} {entry['description']} {entry['content']}"
            )
            if not entry_words:
                continue

            overlap = len(query_words & entry_words)
            if overlap == 0:
                continue

            union = len(query_words | entry_words)
            score = overlap / union if union > 0 else 0.0
            entry["relevance_score"] = round(score, 4)
            results.append(entry)

        # Sort by relevance descending, then by recency
        results.sort(key=lambda e: (e["relevance_score"], -e.get("mtime", 0)), reverse=True)
        return results[:limit]

    def list(self, category: str | None = None) -> list[dict[str, Any]]:
        """List all entries, optionally filtered by category.

        Returns a list of dicts with keys: name, description, type, path, age.
        """
        entries = self._scan_all_entries()

        if category:
            safe_cat = _sanitize_key(category)
            entries = [e for e in entries if e.get("type") == safe_cat]

        # Sort newest first
        entries.sort(key=lambda e: e.get("mtime", 0), reverse=True)
        return entries

    def delete(self, key: str) -> bool:
        """Delete a memory by key. Returns True if found and deleted."""
        safe_key = _sanitize_key(key)

        for cat_dir in self._iter_category_dirs():
            filepath = cat_dir / f"{safe_key}.md"
            if filepath.exists():
                filepath.unlink()
                log.debug("Deleted memory: %s", filepath)
                self._update_index()
                return True

        return False

    def get_stats(self) -> dict[str, Any]:
        """Return statistics about the memory directory.

        Returns dict with keys: entry_count, total_size, categories,
        oldest_entry, newest_entry.
        """
        entries = self._scan_all_entries()
        categories: dict[str, int] = {}
        total_size = 0
        oldest_mtime = float("inf")
        newest_mtime = 0.0

        for entry in entries:
            cat = entry.get("type", "unknown")
            categories[cat] = categories.get(cat, 0) + 1
            total_size += entry.get("size", 0)
            mtime = entry.get("mtime", 0)
            if mtime < oldest_mtime:
                oldest_mtime = mtime
            if mtime > newest_mtime:
                newest_mtime = mtime

        return {
            "entry_count": len(entries),
            "total_size": total_size,
            "total_size_human": _format_size(total_size),
            "categories": categories,
            "oldest_entry": _memory_age_str(oldest_mtime) if entries else None,
            "newest_entry": _memory_age_str(newest_mtime) if entries else None,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _iter_category_dirs(self) -> list[Path]:
        """Return all category subdirectories that exist."""
        if not self._base_dir.is_dir():
            return []
        return [
            d
            for d in sorted(self._base_dir.iterdir())
            if d.is_dir() and not d.name.startswith(".")
        ]

    def _scan_all_entries(self) -> list[dict[str, Any]]:
        """Scan all .md files in the memory directory, returning metadata."""
        results: list[dict[str, Any]] = []
        if not self._base_dir.is_dir():
            return results

        count = 0
        for cat_dir in self._iter_category_dirs():
            for md_file in sorted(cat_dir.glob("*.md")):
                if md_file.name == ENTRYPOINT_NAME:
                    continue
                if count >= MAX_MEMORY_FILES:
                    break

                try:
                    raw = md_file.read_text(encoding="utf-8")
                    meta, body = _parse_frontmatter(raw)
                    stat = md_file.stat()

                    results.append({
                        "name": meta.get("name", md_file.stem),
                        "description": meta.get("description", ""),
                        "type": meta.get("type", cat_dir.name),
                        "content": body,
                        "path": str(md_file),
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                        "age": _memory_age_str(stat.st_mtime),
                        "tags": [
                            t.strip()
                            for t in meta.get("tags", "").split(",")
                            if t.strip()
                        ],
                        "created_at": meta.get("created_at", ""),
                        "updated_at": meta.get("updated_at", ""),
                    })
                    count += 1
                except OSError:
                    continue

        return results

    def _update_index(self) -> None:
        """Regenerate the MEMORY.md entrypoint index."""
        self._base_dir.mkdir(parents=True, exist_ok=True)
        entries = self._scan_all_entries()
        entries.sort(key=lambda e: e.get("mtime", 0), reverse=True)

        lines = ["# Memory Index", ""]
        for entry in entries:
            name = entry.get("name", "untitled")
            desc = entry.get("description", "")
            rel_path = os.path.relpath(entry["path"], self._base_dir)
            hook = f" -- {desc}" if desc else ""
            lines.append(f"- [{name}]({rel_path}){hook}")

        index_path = self._base_dir / ENTRYPOINT_NAME
        index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        log.debug("Updated memory index: %s (%d entries)", index_path, len(entries))

    def load_entrypoint(self) -> str:
        """Load and return the MEMORY.md entrypoint content, truncating if needed."""
        index_path = self._base_dir / ENTRYPOINT_NAME
        if not index_path.exists():
            return f"Your {ENTRYPOINT_NAME} is currently empty."

        raw = index_path.read_text(encoding="utf-8")
        content, was_truncated = _truncate_entrypoint(raw)
        return content


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_memdir: MemoryDirectory | None = None


def get_memory_directory(base_dir: str = "") -> MemoryDirectory:
    """Return (and lazily create) the module-level MemoryDirectory singleton."""
    global _memdir
    if _memdir is None:
        _memdir = MemoryDirectory(base_dir)
    return _memdir
