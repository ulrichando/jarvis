"""
Line-oriented file reader with range selection.

Returns lines [offset, offset + max_lines) from a file.
Strips UTF-8 BOM and normalizes CRLF to LF.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class ReadFileRangeResult:
    """Result of reading a file range."""

    content: str
    line_count: int
    total_lines: int
    total_bytes: int
    read_bytes: int
    mtime_ms: float
    truncated_by_bytes: bool = False


class FileTooLargeError(Exception):
    """Raised when file content exceeds maximum allowed size."""

    def __init__(self, size_in_bytes: int, max_size_bytes: int) -> None:
        self.size_in_bytes = size_in_bytes
        self.max_size_bytes = max_size_bytes
        super().__init__(
            f"File content ({_format_size(size_in_bytes)}) exceeds maximum allowed size "
            f"({_format_size(max_size_bytes)}). Use offset and limit parameters to read "
            f"specific portions of the file, or search for specific content instead."
        )


def _format_size(size: int) -> str:
    """Format byte size to human-readable string."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def read_file_in_range(
    file_path: str,
    offset: int = 0,
    max_lines: Optional[int] = None,
    max_bytes: Optional[int] = None,
    truncate_on_byte_limit: bool = False,
) -> ReadFileRangeResult:
    """
    Read lines [offset, offset + max_lines) from a file.

    Args:
        file_path: Path to the file to read.
        offset: Zero-based line offset to start reading from.
        max_lines: Maximum number of lines to read. None means all.
        max_bytes: Maximum bytes. Behavior depends on truncate_on_byte_limit.
        truncate_on_byte_limit: If True, cap output at max_bytes (no error).
            If False (default), raise FileTooLargeError if file exceeds max_bytes.

    Returns:
        ReadFileRangeResult with the content and metadata.

    Raises:
        FileTooLargeError: If file exceeds max_bytes and truncate_on_byte_limit is False.
        IsADirectoryError: If file_path is a directory.
    """
    stat = os.stat(file_path)

    if os.path.isdir(file_path):
        raise IsADirectoryError(
            f"EISDIR: illegal operation on a directory, read '{file_path}'"
        )

    if not truncate_on_byte_limit and max_bytes is not None and stat.st_size > max_bytes:
        raise FileTooLargeError(stat.st_size, max_bytes)

    mtime_ms = stat.st_mtime * 1000

    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()

    # Strip BOM
    if raw and raw[0] == "\ufeff":
        raw = raw[1:]

    # Normalize CRLF
    if "\r\n" in raw:
        raw = raw.replace("\r\n", "\n")
    if raw.endswith("\r"):
        raw = raw[:-1]

    end_line = offset + max_lines if max_lines is not None else float("inf")

    selected_lines: list[str] = []
    line_index = 0
    selected_bytes = 0
    truncated_by_bytes = False

    for line in raw.split("\n"):
        if line.endswith("\r"):
            line = line[:-1]

        if line_index >= offset and line_index < end_line and not truncated_by_bytes:
            if truncate_on_byte_limit and max_bytes is not None:
                sep = 1 if selected_lines else 0
                next_bytes = selected_bytes + sep + len(line.encode("utf-8"))
                if next_bytes > max_bytes:
                    truncated_by_bytes = True
                else:
                    selected_bytes = next_bytes
                    selected_lines.append(line)
            else:
                selected_lines.append(line)

        line_index += 1

    content = "\n".join(selected_lines)
    total_bytes = len(raw.encode("utf-8"))
    read_bytes = len(content.encode("utf-8"))

    return ReadFileRangeResult(
        content=content,
        line_count=len(selected_lines),
        total_lines=line_index,
        total_bytes=total_bytes,
        read_bytes=read_bytes,
        mtime_ms=mtime_ms,
        truncated_by_bytes=truncated_by_bytes,
    )
