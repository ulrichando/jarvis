"""Hook to fetch current git diff data on demand."""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


MAX_LINES_PER_FILE = 400


@dataclass
class GitDiffStats:
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0


@dataclass
class DiffFile:
    path: str
    lines_added: int = 0
    lines_removed: int = 0
    is_binary: bool = False
    is_large_file: bool = False
    is_truncated: bool = False
    is_new_file: bool = False
    is_untracked: bool = False


@dataclass
class DiffHunk:
    old_start: int = 0
    old_lines: int = 0
    new_start: int = 0
    new_lines: int = 0
    lines: List[str] = field(default_factory=list)


@dataclass
class DiffData:
    stats: Optional[GitDiffStats] = None
    files: List[DiffFile] = field(default_factory=list)
    hunks: Dict[str, List[DiffHunk]] = field(default_factory=dict)
    loading: bool = True


async def fetch_git_diff() -> Optional[dict]:
    """Fetch git diff stats."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "--stat", "--numstat",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return None

        lines = stdout.decode().strip().split("\n")
        stats = GitDiffStats()
        per_file: Dict[str, dict] = {}

        for line in lines:
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) >= 3:
                added_str, removed_str, file_path = parts[0], parts[1], parts[2]
                is_binary = added_str == "-"
                added = int(added_str) if not is_binary else 0
                removed = int(removed_str) if not is_binary else 0
                stats.insertions += added
                stats.deletions += removed
                stats.files_changed += 1
                per_file[file_path] = {
                    "added": added,
                    "removed": removed,
                    "is_binary": is_binary,
                }

        return {"stats": stats, "per_file_stats": per_file}
    except Exception:
        return None


async def load_diff_data() -> DiffData:
    """Fetch both stats and hunks for the current git diff."""
    diff_result = await fetch_git_diff()

    if not diff_result:
        return DiffData(loading=False)

    stats = diff_result["stats"]
    per_file = diff_result["per_file_stats"]
    files: List[DiffFile] = []

    for path, file_stats in per_file.items():
        is_untracked = file_stats.get("is_untracked", False)
        is_binary = file_stats.get("is_binary", False)
        total_lines = file_stats["added"] + file_stats["removed"]
        is_large_file = not is_binary and not is_untracked and total_lines > MAX_LINES_PER_FILE
        is_truncated = not is_large_file and not is_binary and total_lines > MAX_LINES_PER_FILE

        files.append(DiffFile(
            path=path,
            lines_added=file_stats["added"],
            lines_removed=file_stats["removed"],
            is_binary=is_binary,
            is_large_file=is_large_file,
            is_truncated=is_truncated,
            is_untracked=is_untracked,
        ))

    files.sort(key=lambda f: f.path)

    return DiffData(
        stats=stats,
        files=files,
        hunks={},
        loading=False,
    )
