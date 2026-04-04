"""Markdown table renderer for ANSI terminals.

Renders markdown tables with:
- Auto-calculated column widths
- Left/right/center alignment
- Header separator with ANSI bold headers
- Cell truncation for long content
- Vertical format fallback for wide tables
"""

from __future__ import annotations

import os
import re
from typing import Optional

# ANSI codes
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[36m"
GREY = "\033[90m"

ANSI_BOLD_START = BOLD
ANSI_BOLD_END = RESET

SAFETY_MARGIN = 2
MIN_COLUMN_WIDTH = 5
MAX_ROW_LINES = 4

# Regex to strip ANSI sequences for width calculation
_ANSI_RE = re.compile(r'\033\[[0-9;]*m')


def getPlainText(text: str) -> str:
    """Strip ANSI codes and markdown formatting to get plain text width."""
    text = _ANSI_RE.sub("", text)
    # Strip basic markdown inline formatting
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'`(.+?)`', r'\1', text)
    text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
    return text


def _visible_len(text: str) -> int:
    """Get visible length of text (excluding ANSI codes)."""
    return len(getPlainText(text))


def getMinWidth(cells: list[str]) -> int:
    """Get minimum width needed for a column based on content."""
    if not cells:
        return MIN_COLUMN_WIDTH
    # Minimum is the longest single word
    max_word = 0
    for cell in cells:
        plain = getPlainText(cell)
        words = plain.split()
        for w in words:
            max_word = max(max_word, len(w))
    return max(max_word + 2, MIN_COLUMN_WIDTH)


def getIdealWidth(cells: list[str]) -> int:
    """Get ideal width for a column (longest cell content)."""
    if not cells:
        return MIN_COLUMN_WIDTH
    return max(len(getPlainText(c)) + 2 for c in cells)


def wrapText(text: str, width: int) -> list[str]:
    """Wrap text to fit within width, respecting word boundaries."""
    if width <= 0:
        return [text]
    plain = getPlainText(text)
    if len(plain) <= width:
        return [text]

    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        test = f"{current} {word}" if current else word
        if _visible_len(test) <= width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines if lines else [text[:width]]


def formatCell(text: str, width: int, align: str = "left") -> str:
    """Format a cell to a fixed width with alignment.

    Args:
        text: Cell content.
        width: Target width.
        align: 'left', 'right', or 'center'.
    """
    plain_len = _visible_len(text)
    if plain_len > width:
        # Truncate
        plain = getPlainText(text)
        return plain[:width - 1] + "\u2026"

    pad = width - plain_len
    if align == "right":
        return " " * pad + text
    elif align == "center":
        left_pad = pad // 2
        right_pad = pad - left_pad
        return " " * left_pad + text + " " * right_pad
    else:
        return text + " " * pad


def calculateMaxRowLines(rows: list[list[str]], col_widths: list[int]) -> list[int]:
    """Calculate how many lines each row needs when wrapping."""
    result = []
    for row in rows:
        max_lines = 1
        for i, cell in enumerate(row):
            if i < len(col_widths):
                wrapped = wrapText(cell, col_widths[i])
                max_lines = max(max_lines, min(len(wrapped), MAX_ROW_LINES))
        result.append(max_lines)
    return result


def renderRowLines(row: list[str], col_widths: list[int],
                   alignments: list[str], line_idx: int = 0) -> str:
    """Render one line of a potentially multi-line row."""
    cells = []
    for i, cell in enumerate(row):
        w = col_widths[i] if i < len(col_widths) else MIN_COLUMN_WIDTH
        align = alignments[i] if i < len(alignments) else "left"
        wrapped = wrapText(cell, w)
        line_text = wrapped[line_idx] if line_idx < len(wrapped) else ""
        cells.append(formatCell(line_text, w, align))
    return f"{DIM}|{RESET} " + f" {DIM}|{RESET} ".join(cells) + f" {DIM}|{RESET}"


def renderBorderLine(col_widths: list[int], style: str = "middle") -> str:
    """Render a horizontal border line.

    Args:
        col_widths: Width of each column.
        style: 'top', 'middle', or 'bottom'.
    """
    chars = {
        "top": ("\u250c", "\u252c", "\u2510", "\u2500"),
        "middle": ("\u251c", "\u253c", "\u2524", "\u2500"),
        "bottom": ("\u2514", "\u2534", "\u2518", "\u2500"),
    }
    left, cross, right, bar = chars.get(style, chars["middle"])
    segments = [bar * (w + 2) for w in col_widths]
    return f"{DIM}{left}{cross.join(segments)}{right}{RESET}"


def _parse_alignment(separator_row: str) -> list[str]:
    """Parse alignment from separator row like |:---|:---:|---:|."""
    cells = [c.strip() for c in separator_row.strip("|").split("|")]
    alignments = []
    for cell in cells:
        cell = cell.strip()
        if cell.startswith(":") and cell.endswith(":"):
            alignments.append("center")
        elif cell.endswith(":"):
            alignments.append("right")
        else:
            alignments.append("left")
    return alignments


def renderVerticalFormat(headers: list[str], rows: list[list[str]]) -> str:
    """Render table in vertical format when it's too wide for the terminal."""
    output: list[str] = []
    max_header_len = max(len(h) for h in headers) if headers else 0

    for row_idx, row in enumerate(rows):
        if row_idx > 0:
            output.append(f"{DIM}---{RESET}")
        for i, cell in enumerate(row):
            header = headers[i] if i < len(headers) else f"Col {i}"
            padded_header = header.ljust(max_header_len)
            output.append(f"{BOLD}{padded_header}{RESET} : {cell}")
    return "\n".join(output)


def render_table(table_lines: list[str], terminal_width: int = 0) -> str:
    """Render markdown table lines as an ANSI-formatted table.

    Args:
        table_lines: List of markdown table lines (|col|col|...).
        terminal_width: Available width (0 = auto-detect).

    Returns:
        ANSI-formatted table string.
    """
    if not table_lines:
        return ""

    if terminal_width <= 0:
        try:
            terminal_width = os.get_terminal_size().columns
        except OSError:
            terminal_width = 80

    # Parse table
    parsed_rows: list[list[str]] = []
    alignments: list[str] = []
    separator_idx = -1

    for i, line in enumerate(table_lines):
        cells = [c.strip() for c in line.strip("|").split("|")]
        # Check if this is a separator row (|---|---|)
        if all(re.match(r'^:?-+:?$', c.strip()) for c in cells if c.strip()):
            alignments = _parse_alignment(line)
            separator_idx = i
        else:
            parsed_rows.append(cells)

    if not parsed_rows:
        return ""

    headers = parsed_rows[0]
    data_rows = parsed_rows[1:]
    num_cols = len(headers)

    # Default alignments
    if not alignments:
        alignments = ["left"] * num_cols
    while len(alignments) < num_cols:
        alignments.append("left")

    # Calculate column widths
    all_cells_per_col: list[list[str]] = [[] for _ in range(num_cols)]
    for row in parsed_rows:
        for i, cell in enumerate(row):
            if i < num_cols:
                all_cells_per_col[i].append(cell)

    ideal_widths = [getIdealWidth(col) for col in all_cells_per_col]
    total_ideal = sum(ideal_widths) + (num_cols + 1) * 3  # borders + padding

    # If table fits, use ideal widths
    if total_ideal <= terminal_width - SAFETY_MARGIN:
        col_widths = ideal_widths
    else:
        # Try to fit by shrinking columns proportionally
        available = terminal_width - SAFETY_MARGIN - (num_cols + 1) * 3
        if available < num_cols * MIN_COLUMN_WIDTH:
            # Too narrow -- fall back to vertical format
            return renderVerticalFormat(headers, data_rows)

        min_widths = [getMinWidth(col) for col in all_cells_per_col]
        total_min = sum(min_widths)

        if total_min > available:
            col_widths = [max(MIN_COLUMN_WIDTH, available // num_cols)] * num_cols
        else:
            # Distribute extra space proportionally
            col_widths = list(min_widths)
            extra = available - total_min
            total_ideal_extra = sum(ideal_widths[i] - min_widths[i] for i in range(num_cols))
            if total_ideal_extra > 0:
                for i in range(num_cols):
                    share = (ideal_widths[i] - min_widths[i]) / total_ideal_extra
                    col_widths[i] += int(extra * share)

    # Render
    output: list[str] = []
    output.append(renderBorderLine(col_widths, "top"))

    # Header row
    header_cells = []
    for i, h in enumerate(headers):
        w = col_widths[i] if i < len(col_widths) else MIN_COLUMN_WIDTH
        align = alignments[i] if i < len(alignments) else "left"
        header_cells.append(f"{BOLD}{formatCell(h, w, align)}{RESET}")
    output.append(f"{DIM}|{RESET} " + f" {DIM}|{RESET} ".join(header_cells) + f" {DIM}|{RESET}")

    # Header separator
    output.append(renderBorderLine(col_widths, "middle"))

    # Data rows
    for row in data_rows:
        # Pad row to num_cols
        padded = row + [""] * (num_cols - len(row))
        row_lines_needed = 1
        for i, cell in enumerate(padded[:num_cols]):
            w = col_widths[i] if i < len(col_widths) else MIN_COLUMN_WIDTH
            wrapped = wrapText(cell, w)
            row_lines_needed = max(row_lines_needed, min(len(wrapped), MAX_ROW_LINES))

        for line_idx in range(row_lines_needed):
            output.append(renderRowLines(padded[:num_cols], col_widths, alignments, line_idx))

    output.append(renderBorderLine(col_widths, "bottom"))

    return "\n".join(output)


def MarkdownTable(table_lines: list[str], **kwargs) -> str:
    """Render a markdown table. Primary entry point."""
    return render_table(table_lines, **kwargs)


class Props:
    """Properties for table rendering."""
    def __init__(self, lines: Optional[list[str]] = None, width: int = 0):
        self.lines = lines or []
        self.width = width
