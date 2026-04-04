"""
Read file context around a needle match for edit operations.

Scans a file in chunks to find a needle string, then returns
a context window slice containing the match plus surrounding lines.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

CHUNK_SIZE = 8 * 1024
MAX_SCAN_BYTES = 10 * 1024 * 1024


@dataclass
class EditContext:
    """Context around a matched needle in a file."""

    content: str
    """Slice of the file: context_lines before/after the match, on line boundaries."""

    line_offset: int
    """1-based line number of content's first line in the original file."""

    truncated: bool
    """True if MAX_SCAN_BYTES was hit without finding the needle."""


async def read_edit_context(
    path: str,
    needle: str,
    context_lines: int = 3,
) -> Optional[EditContext]:
    """
    Find needle in the file at path and return a context-window slice.

    Returns None on FileNotFoundError.
    Returns EditContext(truncated=True) if needle not found within MAX_SCAN_BYTES.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return _scan_for_context(f, needle, context_lines)
    except FileNotFoundError:
        return None


def _scan_for_context(
    f,
    needle: str,
    context_lines: int,
) -> EditContext:
    """Scan file for needle and return context around the match."""
    if not needle:
        return EditContext(content="", line_offset=1, truncated=False)

    # Read the file content up to MAX_SCAN_BYTES
    content = f.read(MAX_SCAN_BYTES + 1)
    truncated = len(content) > MAX_SCAN_BYTES
    if truncated:
        content = content[:MAX_SCAN_BYTES]

    # Normalize CRLF
    if "\r\n" in content:
        content = content.replace("\r\n", "\n")

    # Try to find the needle (also try CRLF variant)
    match_pos = content.find(needle)
    needle_len = len(needle)

    if match_pos == -1 and "\n" in needle:
        crlf_needle = needle.replace("\n", "\r\n")
        match_pos = content.find(crlf_needle)
        needle_len = len(crlf_needle)

    if match_pos == -1:
        return EditContext(content="", line_offset=1, truncated=truncated)

    # Count lines before match
    lines_before = content[:match_pos].count("\n")

    # Find context start (go back context_lines newlines)
    ctx_start = match_pos
    nl_seen = 0
    while ctx_start > 0 and nl_seen <= context_lines:
        ctx_start -= 1
        if content[ctx_start] == "\n":
            nl_seen += 1
            if nl_seen > context_lines:
                ctx_start += 1  # Don't include the newline itself
                break

    # Find context end (go forward context_lines newlines after match)
    match_end = match_pos + needle_len
    ctx_end = match_end
    nl_seen = 0
    while ctx_end < len(content) and nl_seen < context_lines + 1:
        if content[ctx_end] == "\n":
            nl_seen += 1
            if nl_seen >= context_lines + 1:
                ctx_end += 1
                break
        ctx_end += 1

    # Calculate line offset
    line_offset = lines_before - content[ctx_start:match_pos].count("\n") + 1

    return EditContext(
        content=content[ctx_start:ctx_end],
        line_offset=line_offset,
        truncated=False,
    )
