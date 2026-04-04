"""Structured diff renderer for ANSI terminals.

Renders file diffs with:
- Green for additions (+)
- Red for deletions (-)
- Cyan for @@ hunk headers
- Line numbers in gutter
- File path header
- Context lines (dim)
- Unified diff format support
- edit_file result rendering (old_string -> new_string)
"""

from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass, field
from typing import Optional

from src.components.StructuredDiff.colorDiff import (
    getSyntaxTheme,
    expectColorDiff,
    expectColorFile,
    getColorModuleUnavailableReason,
)

# ANSI codes
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
GREY = "\033[90m"
BG_RED = "\033[41m"
BG_GREEN = "\033[42m"
RED_DIM = "\033[2;31m"
GREEN_DIM = "\033[2;32m"


def computeGutterWidth(max_line: int) -> int:
    """Compute the width of the line number gutter."""
    return max(len(str(max_line)), 3)


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
class DiffLine:
    """A single line in a diff display."""
    type: str  # "add", "remove", "context", "hunk", "header"
    content: str = ""
    old_num: Optional[int] = None
    new_num: Optional[int] = None


def _parse_unified_diff(diff_text: str) -> list[DiffLine]:
    """Parse unified diff text into structured DiffLine objects."""
    lines: list[DiffLine] = []
    old_num = 0
    new_num = 0

    for raw_line in diff_text.split("\n"):
        if raw_line.startswith("---"):
            lines.append(DiffLine(type="header", content=raw_line))
        elif raw_line.startswith("+++"):
            lines.append(DiffLine(type="header", content=raw_line))
        elif raw_line.startswith("@@"):
            match = re.match(r'@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)', raw_line)
            if match:
                old_num = int(match.group(1))
                new_num = int(match.group(2))
                lines.append(DiffLine(type="hunk", content=raw_line))
            else:
                lines.append(DiffLine(type="hunk", content=raw_line))
        elif raw_line.startswith("+"):
            lines.append(DiffLine(type="add", content=raw_line[1:], new_num=new_num))
            new_num += 1
        elif raw_line.startswith("-"):
            lines.append(DiffLine(type="remove", content=raw_line[1:], old_num=old_num))
            old_num += 1
        elif raw_line.startswith(" "):
            lines.append(DiffLine(type="context", content=raw_line[1:],
                                  old_num=old_num, new_num=new_num))
            old_num += 1
            new_num += 1
        elif raw_line.strip():
            lines.append(DiffLine(type="context", content=raw_line,
                                  old_num=old_num, new_num=new_num))
            old_num += 1
            new_num += 1

    return lines


def renderColorDiff(
    diff_text: str,
    file_path: str = "",
    context_lines: int = 3,
) -> str:
    """Render a unified diff string with ANSI colors."""
    if not diff_text.strip():
        return ""

    parsed = _parse_unified_diff(diff_text)
    if not parsed:
        return ""

    max_line = 1
    for dl in parsed:
        if dl.old_num:
            max_line = max(max_line, dl.old_num)
        if dl.new_num:
            max_line = max(max_line, dl.new_num)
    gutter_w = computeGutterWidth(max_line)

    output: list[str] = []

    if file_path:
        short = _shorten_path(file_path)
        output.append(f"  {BOLD}{short}{RESET}")
        output.append(f"  {DIM}{chr(0x2500) * (len(short) + 2)}{RESET}")

    for dl in parsed:
        old_gutter = str(dl.old_num).rjust(gutter_w) if dl.old_num is not None else " " * gutter_w
        new_gutter = str(dl.new_num).rjust(gutter_w) if dl.new_num is not None else " " * gutter_w

        if dl.type == "header":
            output.append(f"  {DIM}{dl.content}{RESET}")
        elif dl.type == "hunk":
            output.append(f"  {CYAN}{dl.content}{RESET}")
        elif dl.type == "add":
            output.append(
                f"  {GREEN_DIM}{' ' * gutter_w}{RESET} {GREEN}{new_gutter}{RESET} "
                f"{GREEN}+{dl.content}{RESET}"
            )
        elif dl.type == "remove":
            output.append(
                f"  {RED_DIM}{old_gutter}{RESET} {RED}{' ' * gutter_w}{RESET} "
                f"{RED}-{dl.content}{RESET}"
            )
        elif dl.type == "context":
            output.append(
                f"  {DIM}{old_gutter} {new_gutter}{RESET}  {DIM}{dl.content}{RESET}"
            )

    return "\n".join(output)


def render_edit_diff(
    old_string: str,
    new_string: str,
    file_path: str = "",
    context_lines: int = 3,
) -> str:
    """Render an edit_file operation as a colored diff."""
    old_lines = old_string.splitlines(keepends=True)
    new_lines = new_string.splitlines(keepends=True)

    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{_shorten_path(file_path)}",
        tofile=f"b/{_shorten_path(file_path)}",
        n=context_lines,
    )
    diff_text = "".join(diff)
    if not diff_text:
        return f"  {DIM}(no changes){RESET}"

    return renderColorDiff(diff_text, file_path)


def render_file_diff(
    file_path: str,
    old_content: str,
    new_content: str,
    context_lines: int = 3,
) -> str:
    """Render a full file diff with ANSI colors."""
    return render_edit_diff(old_content, new_content, file_path, context_lines)


def StructuredDiff(
    diff_text: str = "",
    file_path: str = "",
    old_string: str = "",
    new_string: str = "",
    **kwargs,
) -> str:
    """Primary entry point for diff rendering."""
    if diff_text:
        return renderColorDiff(diff_text, file_path, **kwargs)
    elif old_string or new_string:
        return render_edit_diff(old_string, new_string, file_path, **kwargs)
    return ""


@dataclass
class Props:
    """Properties for diff rendering."""
    diff_text: str = ""
    file_path: str = ""
    old_string: str = ""
    new_string: str = ""
    context_lines: int = 3


class CachedRender:
    """Cache for rendered diffs to avoid re-computing."""

    def __init__(self):
        self._cache: dict[str, str] = {}

    def get(self, key: str, diff_text: str, file_path: str = "") -> str:
        if key not in self._cache:
            self._cache[key] = renderColorDiff(diff_text, file_path)
        return self._cache[key]

    def clear(self):
        self._cache.clear()
