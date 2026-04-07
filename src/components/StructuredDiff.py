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

# True-color support (inherited from terminal env)
_TRUECOLOR = os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")


def _rgb_bg(r: int, g: int, b: int) -> str:
    return f"\033[48;2;{r};{g};{b}m" if _TRUECOLOR else ""


TC_DIFF_ADD_BG = _rgb_bg(34, 92, 43)
TC_DIFF_DEL_BG = _rgb_bg(122, 41, 54)
TC_WORD_ADD_BG = _rgb_bg(56, 166, 96)
TC_WORD_DEL_BG = _rgb_bg(179, 89, 107)

# ── Theme ──────────────────────────────────────────────────────────────────────

_THEME = {
    "add_bg":      TC_DIFF_ADD_BG,
    "del_bg":      TC_DIFF_DEL_BG,
    "word_add_bg": TC_WORD_ADD_BG,
    "word_del_bg": TC_WORD_DEL_BG,
    "add_fg":      GREEN,
    "del_fg":      RED,
    "context_fg":  DIM,
    "hunk_fg":     CYAN,
    "header_fg":   DIM,
}


def getSyntaxTheme() -> dict:
    """Return the active diff syntax-theme dict (truecolor or 8-color fallback)."""
    return dict(_THEME)


def _word_diff(old_line: str, new_line: str) -> tuple[str, str]:
    """Return (old_highlighted, new_highlighted) with word-level change markers."""
    if not _TRUECOLOR:
        return old_line, new_line
    tokens_old = re.split(r"(\W+)", old_line)
    tokens_new = re.split(r"(\W+)", new_line)
    sm = difflib.SequenceMatcher(None, tokens_old, tokens_new, autojunk=False)
    old_out: list[str] = []
    new_out: list[str] = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        seg_old = "".join(tokens_old[i1:i2])
        seg_new = "".join(tokens_new[j1:j2])
        if op == "equal":
            old_out.append(seg_old)
            new_out.append(seg_new)
        elif op == "replace":
            old_out.append(f"{TC_WORD_DEL_BG}{seg_old}{RESET}")
            new_out.append(f"{TC_WORD_ADD_BG}{seg_new}{RESET}")
        elif op == "delete":
            old_out.append(f"{TC_WORD_DEL_BG}{seg_old}{RESET}")
        elif op == "insert":
            new_out.append(f"{TC_WORD_ADD_BG}{seg_new}{RESET}")
    return "".join(old_out), "".join(new_out)


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
            # Parse hunk header: @@ -old_start,old_count +new_start,new_count @@
            match = re.match(r'@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)', raw_line)
            if match:
                old_num = int(match.group(1))
                new_num = int(match.group(2))
                context = match.group(3)
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
    """Render a unified diff string with ANSI colors.

    Args:
        diff_text: Unified diff text.
        file_path: Optional file path for header.
        context_lines: Number of context lines to show.

    Returns:
        ANSI-colored diff string.
    """
    if not diff_text.strip():
        return ""

    parsed = _parse_unified_diff(diff_text)
    if not parsed:
        return ""

    # Find max line numbers for gutter width
    max_line = 1
    for dl in parsed:
        if dl.old_num:
            max_line = max(max_line, dl.old_num)
        if dl.new_num:
            max_line = max(max_line, dl.new_num)
    gutter_w = computeGutterWidth(max_line)

    output: list[str] = []

    # File header
    if file_path:
        short = _shorten_path(file_path)
        output.append(f"  {BOLD}{short}{RESET}")
        output.append(f"  {DIM}{'─' * (len(short) + 2)}{RESET}")

    # Collect lines into remove/add pairs for word-level diff
    # Build a list of (index, DiffLine) pairs for pairing
    pairs: list[tuple[int, int]] = []  # (remove_idx, add_idx) in parsed
    pending_removes: list[int] = []

    for idx, dl in enumerate(parsed):
        if dl.type == "remove":
            pending_removes.append(idx)
        elif dl.type == "add":
            if pending_removes:
                pairs.append((pending_removes.pop(0), idx))
        else:
            pending_removes.clear()

    pair_map: dict[int, int] = {r: a for r, a in pairs}   # remove_idx → add_idx
    pair_map_rev: dict[int, int] = {a: r for r, a in pairs}  # add_idx → remove_idx

    rendered_word_diff: dict[int, str] = {}  # idx → rendered line content

    for r_idx, a_idx in pairs:
        old_hl, new_hl = _word_diff(parsed[r_idx].content, parsed[a_idx].content)
        rendered_word_diff[r_idx] = old_hl
        rendered_word_diff[a_idx] = new_hl

    for idx, dl in enumerate(parsed):
        old_gutter = str(dl.old_num).rjust(gutter_w) if dl.old_num is not None else " " * gutter_w
        new_gutter = str(dl.new_num).rjust(gutter_w) if dl.new_num is not None else " " * gutter_w
        content = rendered_word_diff.get(idx, dl.content)

        if dl.type == "header":
            output.append(f"  {DIM}{dl.content}{RESET}")
        elif dl.type == "hunk":
            output.append(f"  {CYAN}{dl.content}{RESET}")
        elif dl.type == "add":
            add_bg = TC_DIFF_ADD_BG
            output.append(
                f"  {GREEN_DIM}{' ' * gutter_w}{RESET} {GREEN}{new_gutter}{RESET} "
                f"{add_bg}{GREEN}+{content}{RESET}"
            )
        elif dl.type == "remove":
            del_bg = TC_DIFF_DEL_BG
            output.append(
                f"  {RED_DIM}{old_gutter}{RESET} {RED}{' ' * gutter_w}{RESET} "
                f"{del_bg}{RED}-{content}{RESET}"
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
    """Render an edit_file operation as a colored diff.

    Generates a unified diff from old_string -> new_string and renders it.

    Args:
        old_string: Original text.
        new_string: Replacement text.
        file_path: File being edited.
        context_lines: Lines of context around changes.

    Returns:
        ANSI-colored diff string.
    """
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
    """Render a full file diff with ANSI colors.

    Args:
        file_path: Path of the file.
        old_content: Previous file content.
        new_content: New file content.
        context_lines: Lines of context.

    Returns:
        ANSI-colored diff string.
    """
    return render_edit_diff(old_content, new_content, file_path, context_lines)


def StructuredDiff(
    diff_text: str = "",
    file_path: str = "",
    old_string: str = "",
    new_string: str = "",
    **kwargs,
) -> str:
    """Primary entry point for diff rendering.

    Can accept either raw diff text or old/new strings.
    """
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
