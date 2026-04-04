"""File edit diff display for ANSI terminals.

Renders file edits (old_string -> new_string) as colored diffs with:
- File path header
- Old text in red, new text in green
- Context lines around the change
- Line numbers
"""

from __future__ import annotations

import difflib
import os
from dataclasses import dataclass
from typing import Optional

# ANSI codes
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
CYAN = "\033[36m"
GREY = "\033[90m"
YELLOW = "\033[33m"


def _shorten_path(path: str, max_len: int = 60) -> str:
    """Shorten a file path for display."""
    if not path:
        return ""
    try:
        cwd = os.getcwd()
        if path.startswith(cwd):
            path = path[len(cwd):].lstrip("/")
    except Exception:
        pass
    if len(path) > max_len:
        half = (max_len - 3) // 2
        path = path[:half] + "\u2026" + path[-half:]
    return path


@dataclass
class DiffData:
    """Parsed diff data for display."""
    file_path: str = ""
    old_string: str = ""
    new_string: str = ""
    old_start_line: int = 1
    context_before: list[str] | None = None
    context_after: list[str] | None = None
    success: bool = True
    error: str = ""


@dataclass
class Props:
    """Properties for FileEditToolDiff."""
    file_path: str = ""
    old_string: str = ""
    new_string: str = ""
    file_content: str = ""


def normalizeEdit(old_string: str, new_string: str) -> tuple[str, str]:
    """Normalize edit strings (strip trailing newlines for comparison)."""
    # Ensure consistent line endings
    old = old_string.replace("\r\n", "\n")
    new = new_string.replace("\r\n", "\n")
    return old, new


def loadDiffData(
    file_path: str,
    old_string: str,
    new_string: str,
    file_content: str = "",
    context_lines: int = 3,
) -> DiffData:
    """Load and prepare diff data with context from file content.

    Args:
        file_path: Path to the file being edited.
        old_string: Original text being replaced.
        new_string: Replacement text.
        file_content: Full file content (for context extraction).
        context_lines: Number of context lines to show.

    Returns:
        DiffData with context lines populated.
    """
    old_string, new_string = normalizeEdit(old_string, new_string)
    data = DiffData(
        file_path=file_path,
        old_string=old_string,
        new_string=new_string,
    )

    if file_content:
        file_lines = file_content.split("\n")
        old_lines = old_string.split("\n")

        # Find where old_string starts in file
        for i in range(len(file_lines)):
            match = True
            for j, old_line in enumerate(old_lines):
                if i + j >= len(file_lines) or file_lines[i + j] != old_line:
                    match = False
                    break
            if match:
                data.old_start_line = i + 1
                # Extract context
                start = max(0, i - context_lines)
                end = min(len(file_lines), i + len(old_lines) + context_lines)
                data.context_before = file_lines[start:i]
                data.context_after = file_lines[i + len(old_lines):end]
                break

    return data


def diffToolInputsOnly(old_string: str, new_string: str) -> str:
    """Generate a simple diff from just old/new strings (no file context)."""
    return _render_diff(old_string, new_string)


def _render_diff(
    old_string: str,
    new_string: str,
    file_path: str = "",
    start_line: int = 1,
    context_before: list[str] | None = None,
    context_after: list[str] | None = None,
) -> str:
    """Render a diff between old and new strings with ANSI colors.

    Returns:
        ANSI-formatted diff string.
    """
    old_lines = old_string.split("\n")
    new_lines = new_string.split("\n")

    output: list[str] = []

    # File header
    if file_path:
        short = _shorten_path(file_path)
        output.append(f"  {BOLD}{short}{RESET}")
        output.append(f"  {DIM}{'─' * (len(short) + 2)}{RESET}")

    # Compute line number gutter width
    max_line = start_line + max(len(old_lines), len(new_lines)) + len(context_before or []) + len(context_after or [])
    gutter_w = max(len(str(max_line)), 3)
    line_num = start_line

    # Context before
    if context_before:
        ctx_start = line_num - len(context_before)
        for i, ctx_line in enumerate(context_before):
            ln = ctx_start + i
            output.append(f"  {DIM}{str(ln).rjust(gutter_w)}  {ctx_line}{RESET}")

    # Diff using difflib for fine-grained changes
    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=0))

    # Show removed lines
    removed_start = line_num
    for i, old_line in enumerate(old_lines):
        ln = removed_start + i
        output.append(f"  {RED}{str(ln).rjust(gutter_w)} -{old_line}{RESET}")

    # Show added lines
    for i, new_line in enumerate(new_lines):
        ln = removed_start + i
        output.append(f"  {GREEN}{str(ln).rjust(gutter_w)} +{new_line}{RESET}")

    line_num += max(len(old_lines), len(new_lines))

    # Context after
    if context_after:
        for i, ctx_line in enumerate(context_after):
            ln = start_line + len(old_lines) + i
            output.append(f"  {DIM}{str(ln).rjust(gutter_w)}  {ctx_line}{RESET}")

    return "\n".join(output)


def DiffBody(data: DiffData) -> str:
    """Render the diff body from DiffData."""
    return _render_diff(
        data.old_string,
        data.new_string,
        data.file_path,
        data.old_start_line,
        data.context_before,
        data.context_after,
    )


def DiffFrame(data: DiffData) -> str:
    """Render a framed diff (with box drawing)."""
    body = DiffBody(data)
    if not body:
        return ""

    output: list[str] = []
    short = _shorten_path(data.file_path)

    # Top border
    output.append(f"  {DIM}╭─ {RESET}{BOLD}{short}{RESET}{DIM} ─{'─' * max(0, 40 - len(short))}╮{RESET}")

    for line in body.split("\n"):
        output.append(f"  {DIM}│{RESET} {line}")

    # Status
    old_count = len(data.old_string.split("\n"))
    new_count = len(data.new_string.split("\n"))
    status = f"{RED}-{old_count}{RESET} {GREEN}+{new_count}{RESET} lines"
    output.append(f"  {DIM}╰─ {status} ─{'─' * 30}╯{RESET}")

    return "\n".join(output)


def FileEditToolDiff(
    file_path: str = "",
    old_string: str = "",
    new_string: str = "",
    file_content: str = "",
    context_lines: int = 3,
    framed: bool = False,
) -> str:
    """Render a file edit as a colored diff. Primary entry point.

    Args:
        file_path: Path to the file being edited.
        old_string: Original text.
        new_string: Replacement text.
        file_content: Full file content (for context).
        context_lines: Number of context lines.
        framed: Whether to draw a frame around the diff.

    Returns:
        ANSI-formatted diff string.
    """
    if not old_string and not new_string:
        return f"  {DIM}(no changes){RESET}"

    data = loadDiffData(file_path, old_string, new_string, file_content, context_lines)

    if framed:
        return DiffFrame(data)
    return DiffBody(data)
