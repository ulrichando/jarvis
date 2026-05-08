"""Read — file reader matching claude-code's FileReadTool spec.

Direct in-process replacement for the legacy ~8 KB `read_file` tool. The
LLM gets the same description + usage rules it would in claude-code, so
the same mental model carries over.

Voice channel notes:
  - Path tracking via _track_read() lets the Edit/Write tools enforce the
    "you must read before you edit" invariant. Tracking is per-process,
    cleared on voice-agent restart.
  - Output uses cat -n format ("   42→content") so the LLM can reference
    line numbers when proposing an Edit. Prefix matches claude-code's
    legacy compact-line format.
  - 2 000-line default cap; offset/limit params for chunking.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from livekit.agents.llm import function_tool

logger = logging.getLogger("jarvis.read")

MAX_LINES_TO_READ = 2_000
# Hard char cap — TTS / context window safety. A single 2 000-line file
# of ordinary code is ~80 KB; a 1 MB README would explode the prompt.
MAX_OUTPUT_CHARS = 256 * 1024

# Module-scoped read tracker. {abs_path: mtime_ns_at_read}. Edit/Write
# consult this to enforce read-first. Cleared on process restart, which is
# the right scope — voice sessions don't span restarts.
_READS: dict[str, int] = {}


def _track_read(abs_path: str, mtime_ns: int) -> None:
    _READS[abs_path] = mtime_ns


def has_been_read(abs_path: str) -> bool:
    """Has this exact path been read in this voice-agent session?
    Used by Edit and Write to enforce claude-code's read-first invariant.
    """
    return abs_path in _READS


def read_mtime_ns(abs_path: str) -> Optional[int]:
    """Returns the mtime_ns recorded at read time, or None if never read.
    Edit/Write use this to detect external modification ("the file
    changed since you read it")."""
    return _READS.get(abs_path)


def mark_written(abs_path: str, mtime_ns: int) -> None:
    """After a successful Edit/Write, refresh the tracker so subsequent
    edits don't trip the read-first check on a file the agent itself
    just touched."""
    _READS[abs_path] = mtime_ns


def _format_lines(text: str, start_line: int) -> str:
    """cat -n style: line number padded to 6 cols, tab, content. Matches
    claude-code's compact-line-prefix format that Edit looks for."""
    out = []
    for i, line in enumerate(text.splitlines(), start=start_line):
        out.append(f"{i:>6}\t{line}")
    return "\n".join(out)


@function_tool
async def read(
    file_path: str,
    offset: int = 0,
    limit: int = MAX_LINES_TO_READ,
) -> str:
    """Read a file from the local filesystem.

    Reads a file from the local filesystem. You can access any file
    directly by using this tool. Assume this tool is able to read all
    files on the machine. If the User provides a path to a file assume
    that path is valid. It is okay to read a file that does not exist;
    an error will be returned.

    Usage:
      - The file_path parameter must be an absolute path, not a relative
        path.
      - By default, it reads up to 2000 lines starting from the
        beginning of the file.
      - When you already know which part of the file you need, only read
        that part — pass `offset` (1-indexed line) and `limit` (line
        count). Important for large files.
      - Results are returned using cat -n format, with line numbers
        starting at 1.
      - This tool can only read files, not directories. To list a
        directory, use the bash tool with `ls`.
      - If you read a file that exists but has empty contents, you will
        receive a `[empty file]` marker.
      - Binary files are detected and a marker is returned instead of
        garbage bytes.

    Args:
        file_path: Absolute path to the file to read.
        offset:    1-indexed line to start at. 0 (default) means
                   start of file.
        limit:     Max number of lines to return. Default 2000.
    """
    fp = (file_path or "").strip()
    if not fp:
        return "Error: file_path is required."
    if not os.path.isabs(fp):
        return f"Error: file_path must be absolute, got: {fp}"

    p = Path(fp)
    if not p.exists():
        return f"Error: file does not exist: {fp}"
    if p.is_dir():
        return f"Error: {fp} is a directory, not a file. Use bash with `ls {fp}` to list its contents."

    try:
        st = p.stat()
        mtime_ns = st.st_mtime_ns
    except OSError as e:
        return f"Error: could not stat file: {type(e).__name__}: {e}"

    # Cheap binary sniff — scan first 4 KB for null bytes.
    try:
        with open(p, "rb") as fh:
            head = fh.read(4096)
        if b"\x00" in head:
            return f"[binary file: {fp} — {st.st_size} bytes. Use a dedicated tool to inspect.]"
    except OSError as e:
        return f"Error: could not read file: {type(e).__name__}: {e}"

    # Empty?
    if st.st_size == 0:
        _track_read(fp, mtime_ns)
        return "[empty file]"

    # Read text content. Cap by chars + lines.
    try:
        # Read all text up to MAX_OUTPUT_CHARS. For very large files we
        # still bail with a marker.
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read(MAX_OUTPUT_CHARS + 1)
        oversize = len(content) > MAX_OUTPUT_CHARS
        if oversize:
            content = content[:MAX_OUTPUT_CHARS]
    except OSError as e:
        return f"Error: could not read file: {type(e).__name__}: {e}"

    # Apply offset/limit. offset is 1-indexed.
    try:
        off = max(0, int(offset or 0))
        lim = max(1, min(int(limit or MAX_LINES_TO_READ), MAX_LINES_TO_READ * 2))
    except Exception:
        off = 0
        lim = MAX_LINES_TO_READ

    lines = content.splitlines()
    total_lines = len(lines)
    start = max(1, off) if off else 1
    end = min(total_lines, start + lim - 1)
    chunk = lines[start - 1 : end]

    formatted = _format_lines("\n".join(chunk), start)

    # Track read so Edit/Write can enforce read-first.
    _track_read(fp, mtime_ns)

    suffix_parts = []
    if end < total_lines:
        suffix_parts.append(
            f"[showing lines {start}-{end} of {total_lines}; "
            f"use offset={end + 1} to continue]"
        )
    if oversize:
        suffix_parts.append(
            "[file is larger than the char cap; some content past line "
            f"{end} is unread]"
        )
    if suffix_parts:
        formatted = formatted + "\n\n" + "\n".join(suffix_parts)

    logger.info(f"read → {fp} (lines {start}-{end}/{total_lines})")
    return formatted
