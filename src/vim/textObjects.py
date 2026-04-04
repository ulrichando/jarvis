"""Vim text objects (iw, aw, i(, a(, etc.)."""

from __future__ import annotations

from typing import Optional

from .operators import TextRange


def inner_word(text: str, cursor: int) -> Optional[TextRange]:
    """Get the inner word text object at cursor position."""
    if cursor >= len(text):
        return None
    start = cursor
    while start > 0 and text[start - 1].isalnum():
        start -= 1
    end = cursor
    while end < len(text) and text[end].isalnum():
        end += 1
    if start == end:
        return None
    return TextRange(start=start, end=end)


def a_word(text: str, cursor: int) -> Optional[TextRange]:
    """Get the around-word text object at cursor position."""
    result = inner_word(text, cursor)
    if not result:
        return None
    end = result.end
    while end < len(text) and text[end] == " ":
        end += 1
    return TextRange(start=result.start, end=end)


def inner_paren(text: str, cursor: int, open_char: str = "(", close_char: str = ")") -> Optional[TextRange]:
    """Get the inner parenthesis text object."""
    depth = 0
    start = cursor
    while start >= 0:
        if text[start] == open_char:
            if depth == 0:
                break
            depth -= 1
        elif text[start] == close_char:
            depth += 1
        start -= 1
    if start < 0:
        return None

    depth = 0
    end = cursor
    while end < len(text):
        if text[end] == close_char:
            if depth == 0:
                break
            depth -= 1
        elif text[end] == open_char:
            depth += 1
        end += 1
    if end >= len(text):
        return None

    return TextRange(start=start + 1, end=end)
