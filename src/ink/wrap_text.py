"""Text wrapping and truncation for terminal output."""

from __future__ import annotations

import textwrap

from .string_width import string_width

ELLIPSIS = "\u2026"


def _truncate(text: str, columns: int, position: str) -> str:
    """Truncate text to fit within columns, adding an ellipsis."""
    if columns < 1:
        return ""
    if columns == 1:
        return ELLIPSIS

    length = string_width(text)
    if length <= columns:
        return text

    if position == "start":
        # Take from end
        result = text[-(columns - 1):]
        return ELLIPSIS + result
    if position == "middle":
        half = columns // 2
        return text[:half] + ELLIPSIS + text[-(columns - half - 1):]
    # end
    return text[:columns - 1] + ELLIPSIS


def _wrap_ansi(text: str, max_width: int, trim: bool = False, hard: bool = True) -> str:
    """Wrap text to max_width columns. Simple implementation."""
    if max_width <= 0:
        return text

    lines = text.split("\n")
    result_lines: list[str] = []

    for line in lines:
        if string_width(line) <= max_width:
            result_lines.append(line.rstrip() if trim else line)
        elif hard:
            # Hard wrap at max_width
            current = ""
            current_width = 0
            for ch in line:
                ch_width = string_width(ch)
                if current_width + ch_width > max_width and current:
                    result_lines.append(current.rstrip() if trim else current)
                    current = ch
                    current_width = ch_width
                else:
                    current += ch
                    current_width += ch_width
            if current:
                result_lines.append(current.rstrip() if trim else current)
        else:
            result_lines.append(line.rstrip() if trim else line)

    return "\n".join(result_lines)


def wrap_text(text: str, max_width: int, wrap_type: str | None) -> str:
    """Wrap or truncate text according to the wrap type."""
    if wrap_type == "wrap":
        return _wrap_ansi(text, max_width, trim=False, hard=True)

    if wrap_type == "wrap-trim":
        return _wrap_ansi(text, max_width, trim=True, hard=True)

    if wrap_type and wrap_type.startswith("truncate"):
        position = "end"
        if wrap_type == "truncate-middle":
            position = "middle"
        elif wrap_type == "truncate-start":
            position = "start"
        return _truncate(text, max_width, position)

    return text
