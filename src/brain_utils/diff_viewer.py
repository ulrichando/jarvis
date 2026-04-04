"""Unified diff parser, formatter, and word-level diff highlighting.

Converts raw unified diff text into structured data, then renders it
with ANSI colors for terminal display. Handles DiffDetailView,
DiffFileList, and StructuredDiff components.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import List, Optional, Tuple

# ── ANSI codes (matching shells/cli/display.py) ──────────────────────
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RED_BG = "\033[41m"
GREEN_BG = "\033[42m"

# Regex for unified diff hunk headers: @@ -old_start,old_count +new_start,new_count @@
_HUNK_RE = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$"
)
_DIFF_HEADER_RE = re.compile(r"^diff --git a/(.*) b/(.*)$")
_BINARY_RE = re.compile(r"^Binary files .* differ$")


# ── Data structures ──────────────────────────────────────────────────

@dataclass
class DiffLine:
    """A single line within a hunk."""
    marker: str          # "+", "-", or " " (context)
    content: str         # line content without the marker

    def __str__(self) -> str:
        return f"{self.marker}{self.content}"


@dataclass
class Hunk:
    """One @@ hunk within a file diff."""
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header_extra: str = ""      # text after the @@ markers (e.g. function name)
    lines: List[DiffLine] = field(default_factory=list)


@dataclass
class FileDiff:
    """Diff data for a single file."""
    path: str
    lines_added: int = 0
    lines_removed: int = 0
    is_binary: bool = False
    is_new: bool = False
    is_deleted: bool = False
    hunks: List[Hunk] = field(default_factory=list)


@dataclass
class DiffData:
    """Top-level container for a parsed multi-file diff."""
    files: List[FileDiff] = field(default_factory=list)

    @property
    def total_added(self) -> int:
        return sum(f.lines_added for f in self.files)

    @property
    def total_removed(self) -> int:
        return sum(f.lines_removed for f in self.files)


# ── Parsing ──────────────────────────────────────────────────────────

def parse_diff(diff_text: str) -> DiffData:
    """Parse a unified diff string into structured *DiffData*.

    Handles standard ``git diff`` output including binary markers,
    new/deleted file modes, and rename headers.
    """
    data = DiffData()
    current_file: Optional[FileDiff] = None
    current_hunk: Optional[Hunk] = None
    lines = diff_text.splitlines()
    i = 0

    while i < len(lines):
        line = lines[i]

        # ── diff --git header ────────────────────────────────────────
        m = _DIFF_HEADER_RE.match(line)
        if m:
            path = m.group(2)
            current_file = FileDiff(path=path)
            data.files.append(current_file)
            current_hunk = None
            i += 1
            continue

        # ── new / deleted file mode ──────────────────────────────────
        if current_file is not None:
            if line.startswith("new file mode"):
                current_file.is_new = True
                i += 1
                continue
            if line.startswith("deleted file mode"):
                current_file.is_deleted = True
                i += 1
                continue

        # ── binary marker ────────────────────────────────────────────
        if _BINARY_RE.match(line):
            if current_file is not None:
                current_file.is_binary = True
            i += 1
            continue

        # ── --- / +++ headers (skip, path already captured) ──────────
        if line.startswith("--- ") or line.startswith("+++ "):
            # If we haven't seen a diff --git header yet, derive path
            if current_file is None and line.startswith("+++ "):
                path = line[4:].lstrip("b/")
                current_file = FileDiff(path=path)
                data.files.append(current_file)
            i += 1
            continue

        # ── hunk header ──────────────────────────────────────────────
        hm = _HUNK_RE.match(line)
        if hm:
            if current_file is None:
                current_file = FileDiff(path="<unknown>")
                data.files.append(current_file)
            hunk = Hunk(
                old_start=int(hm.group(1)),
                old_count=int(hm.group(2)) if hm.group(2) else 1,
                new_start=int(hm.group(3)),
                new_count=int(hm.group(4)) if hm.group(4) else 1,
                header_extra=hm.group(5).strip(),
            )
            current_file.hunks.append(hunk)
            current_hunk = hunk
            i += 1
            continue

        # ── diff lines (+, -, context) ───────────────────────────────
        if current_hunk is not None:
            if line.startswith("+"):
                dl = DiffLine(marker="+", content=line[1:])
                current_hunk.lines.append(dl)
                if current_file:
                    current_file.lines_added += 1
                i += 1
                continue
            if line.startswith("-"):
                dl = DiffLine(marker="-", content=line[1:])
                current_hunk.lines.append(dl)
                if current_file:
                    current_file.lines_removed += 1
                i += 1
                continue
            if line.startswith(" ") or line == "":
                dl = DiffLine(marker=" ", content=line[1:] if line.startswith(" ") else "")
                current_hunk.lines.append(dl)
                i += 1
                continue
            # "\No newline at end of file" or similar
            if line.startswith("\\"):
                i += 1
                continue

        # ── skip unrecognised lines (index, mode, similarity, etc.) ──
        i += 1

    return data


# ── Word-level diff ──────────────────────────────────────────────────

def word_diff(old_line: str, new_line: str, color: bool = True) -> Tuple[str, str]:
    """Compute word-level diff between two lines.

    Returns a tuple of (formatted_old, formatted_new) with inline
    highlights on the changed segments.  When *color* is False the
    changed parts are wrapped in ``[...]`` / ``{...}`` brackets instead.
    """
    sm = SequenceMatcher(None, old_line, new_line)
    old_parts: list[str] = []
    new_parts: list[str] = []

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        old_seg = old_line[i1:i2]
        new_seg = new_line[j1:j2]

        if tag == "equal":
            old_parts.append(old_seg)
            new_parts.append(new_seg)
        elif tag == "replace":
            if color:
                old_parts.append(f"{RED_BG}{old_seg}{RESET}{RED}")
                new_parts.append(f"{GREEN_BG}{new_seg}{RESET}{GREEN}")
            else:
                old_parts.append(f"[{old_seg}]")
                new_parts.append(f"{{{new_seg}}}")
        elif tag == "delete":
            if color:
                old_parts.append(f"{RED_BG}{old_seg}{RESET}{RED}")
            else:
                old_parts.append(f"[{old_seg}]")
        elif tag == "insert":
            if color:
                new_parts.append(f"{GREEN_BG}{new_seg}{RESET}{GREEN}")
            else:
                new_parts.append(f"{{{new_seg}}}")

    return "".join(old_parts), "".join(new_parts)


# ── Formatting ───────────────────────────────────────────────────────

def _file_header(f: FileDiff, color: bool) -> str:
    """Render a single file header line with +N/-N stats."""
    stats_parts = []
    if f.lines_added:
        s = f"+{f.lines_added}"
        stats_parts.append(f"{GREEN}{s}{RESET}" if color else s)
    if f.lines_removed:
        s = f"-{f.lines_removed}"
        stats_parts.append(f"{RED}{s}{RESET}" if color else s)
    stats = " ".join(stats_parts)

    tag = ""
    if f.is_new:
        tag = " (new)"
    elif f.is_deleted:
        tag = " (deleted)"
    elif f.is_binary:
        tag = " (binary)"

    path = f.path
    if color:
        path = f"{BOLD}{path}{RESET}"

    return f"{path}{tag}  {stats}" if stats else f"{path}{tag}"


def format_diff(diff_data: DiffData, color: bool = True) -> str:
    """Render a full diff for terminal display.

    Green for additions, red for removals, dim for context lines.
    File headers show +N/-N stats.  Hunk headers use @@ markers.
    """
    out: list[str] = []

    for f in diff_data.files:
        out.append(_file_header(f, color))

        if f.is_binary:
            line = "  Binary file differs"
            out.append(f"{DIM}{line}{RESET}" if color else line)
            out.append("")
            continue

        for hunk in f.hunks:
            hdr = f"@@ -{hunk.old_start},{hunk.old_count} +{hunk.new_start},{hunk.new_count} @@"
            if hunk.header_extra:
                hdr += f" {hunk.header_extra}"
            out.append(f"{CYAN}{hdr}{RESET}" if color else hdr)

            # Collect consecutive -/+ pairs for word-level highlighting
            i = 0
            hunk_lines = hunk.lines
            while i < len(hunk_lines):
                dl = hunk_lines[i]

                if dl.marker == " ":
                    line = f" {dl.content}"
                    out.append(f"{DIM}{line}{RESET}" if color else line)
                    i += 1
                elif dl.marker == "-":
                    # Look ahead for a matching "+" line for word diff
                    if (color and i + 1 < len(hunk_lines)
                            and hunk_lines[i + 1].marker == "+"):
                        old_wd, new_wd = word_diff(dl.content, hunk_lines[i + 1].content, color)
                        out.append(f"{RED}-{old_wd}{RESET}")
                        out.append(f"{GREEN}+{new_wd}{RESET}")
                        i += 2
                    else:
                        line = f"-{dl.content}"
                        out.append(f"{RED}{line}{RESET}" if color else line)
                        i += 1
                elif dl.marker == "+":
                    line = f"+{dl.content}"
                    out.append(f"{GREEN}{line}{RESET}" if color else line)
                    i += 1
                else:
                    out.append(f" {dl.content}")
                    i += 1

        out.append("")  # blank line between files

    return "\n".join(out)


def diff_summary(diff_data: DiffData) -> str:
    """One-line summary like ``3 files changed, +42 -17``."""
    n_files = len(diff_data.files)
    added = diff_data.total_added
    removed = diff_data.total_removed

    file_word = "file" if n_files == 1 else "files"
    parts = [f"{n_files} {file_word} changed"]
    if added:
        parts.append(f"+{added}")
    if removed:
        parts.append(f"-{removed}")
    return ", ".join(parts)
