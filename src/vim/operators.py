"""Vim operators (d, c, y, etc.)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class TextRange:
    """A range of text positions."""
    start: int
    end: int


@dataclass
class OperatorResult:
    text: str
    new_cursor: int
    register_content: Optional[str] = None


def delete_range(text: str, text_range: TextRange) -> OperatorResult:
    """Delete text in the given range."""
    deleted = text[text_range.start:text_range.end]
    new_text = text[:text_range.start] + text[text_range.end:]
    return OperatorResult(
        text=new_text,
        new_cursor=text_range.start,
        register_content=deleted,
    )


def yank_range(text: str, text_range: TextRange) -> OperatorResult:
    """Yank (copy) text in the given range."""
    yanked = text[text_range.start:text_range.end]
    return OperatorResult(
        text=text,
        new_cursor=text_range.start,
        register_content=yanked,
    )


def change_range(text: str, text_range: TextRange) -> OperatorResult:
    """Change (delete and enter insert mode) text in the given range."""
    return delete_range(text, text_range)
