"""
Text rendering utilities for terminal display.

Provides truncated content rendering with line limits and wrapping.
"""

from typing import Tuple

MAX_LINES_TO_SHOW = 3
PADDING_TO_PREVENT_OVERFLOW = 10


def _string_width(text: str) -> int:
    """Visible width of text (simple approximation)."""
    # Strip ANSI escape codes for width calculation
    import re
    clean = re.sub(r"\033\[[0-9;]*m", "", text)
    return len(clean)


def _wrap_text(text: str, wrap_width: int) -> Tuple[str, int]:
    """
    Wrap text at the specified width and return above-the-fold content
    plus remaining line count.
    """
    lines = text.split("\n")
    wrapped_lines = []

    for line in lines:
        visible_width = _string_width(line)
        if visible_width <= wrap_width:
            wrapped_lines.append(line.rstrip())
        else:
            position = 0
            while position < len(line):
                chunk = line[position:position + wrap_width]
                wrapped_lines.append(chunk.rstrip())
                position += wrap_width

    remaining_lines = len(wrapped_lines) - MAX_LINES_TO_SHOW

    # If there's only 1 line after the fold, show it directly
    if remaining_lines == 1:
        above = "\n".join(wrapped_lines[:MAX_LINES_TO_SHOW + 1]).rstrip()
        return above, 0

    above = "\n".join(wrapped_lines[:MAX_LINES_TO_SHOW]).rstrip()
    return above, max(0, remaining_lines)


def render_truncated_content(
    content: str,
    terminal_width: int,
    suppress_expand_hint: bool = False,
) -> str:
    """
    Renders content with line-based truncation for terminal display.

    If the content exceeds the maximum number of lines, it truncates
    and adds a message indicating additional lines.
    """
    trimmed = content.rstrip()
    if not trimmed:
        return ""

    wrap_width = max(terminal_width - PADDING_TO_PREVENT_OVERFLOW, 10)

    # Only process enough content for visible lines
    max_chars = MAX_LINES_TO_SHOW * wrap_width * 4
    pre_truncated = len(trimmed) > max_chars
    content_for_wrapping = trimmed[:max_chars] if pre_truncated else trimmed

    above_the_fold, remaining_lines = _wrap_text(content_for_wrapping, wrap_width)

    estimated_remaining = remaining_lines
    if pre_truncated:
        estimated_remaining = max(
            remaining_lines,
            len(trimmed) // wrap_width - MAX_LINES_TO_SHOW,
        )

    parts = [above_the_fold]
    if estimated_remaining > 0:
        hint = "" if suppress_expand_hint else " (ctrl+o to expand)"
        parts.append(f"... +{estimated_remaining} lines{hint}")

    return "\n".join(p for p in parts if p)


def is_output_line_truncated(content: str) -> bool:
    """
    Fast check: would render_truncated_content truncate this content?

    Counts raw newlines only (ignores terminal-width wrapping).
    """
    pos = 0
    for _ in range(MAX_LINES_TO_SHOW + 1):
        pos = content.find("\n", pos)
        if pos == -1:
            return False
        pos += 1
    return pos < len(content)
