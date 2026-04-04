"""
Diff utilities for computing and displaying file differences.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Optional


CONTEXT_LINES = 3
DIFF_TIMEOUT_MS = 5000


@dataclass
class StructuredPatchHunk:
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    lines: list[str]


def adjust_hunk_line_numbers(
    hunks: list[StructuredPatchHunk], offset: int
) -> list[StructuredPatchHunk]:
    """
    Shifts hunk line numbers by offset. Use when the patch was computed from
    a slice of the file rather than the whole file.
    """
    if offset == 0:
        return hunks
    return [
        StructuredPatchHunk(
            old_start=h.old_start + offset,
            old_lines=h.old_lines,
            new_start=h.new_start + offset,
            new_lines=h.new_lines,
            lines=h.lines,
        )
        for h in hunks
    ]


def count_lines_changed(
    patch: list[StructuredPatchHunk],
    new_file_content: Optional[str] = None,
) -> tuple[int, int]:
    """
    Count lines added and removed in a patch.

    Args:
        patch: Array of diff hunks.
        new_file_content: Optional content string for new files.

    Returns:
        Tuple of (additions, removals).
    """
    if not patch and new_file_content:
        num_additions = len(new_file_content.splitlines())
        return num_additions, 0

    num_additions = sum(
        sum(1 for line in hunk.lines if line.startswith("+")) for hunk in patch
    )
    num_removals = sum(
        sum(1 for line in hunk.lines if line.startswith("-")) for hunk in patch
    )
    return num_additions, num_removals


def get_unified_diff(
    old_text: str,
    new_text: str,
    filename: str = "",
    context_lines: int = CONTEXT_LINES,
) -> str:
    """
    Generate a unified diff between two strings.

    Args:
        old_text: Original text.
        new_text: Modified text.
        filename: File name for the diff header.
        context_lines: Number of context lines.

    Returns:
        Unified diff string.
    """
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)

    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{filename}" if filename else "a",
        tofile=f"b/{filename}" if filename else "b",
        n=context_lines,
    )
    return "".join(diff)
