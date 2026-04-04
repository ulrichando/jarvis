"""
Vim Motion Functions

Pure functions for resolving vim motions to cursor positions.

Converted from motions.ts to Python.
"""

from __future__ import annotations

from typing import Protocol


class Cursor(Protocol):
    """Protocol for a cursor object with vim-style movement methods."""

    @property
    def offset(self) -> int: ...

    def equals(self, other: "Cursor") -> bool: ...
    def left(self) -> "Cursor": ...
    def right(self) -> "Cursor": ...
    def down(self) -> "Cursor": ...
    def up(self) -> "Cursor": ...
    def down_logical_line(self) -> "Cursor": ...
    def up_logical_line(self) -> "Cursor": ...
    def next_vim_word(self) -> "Cursor": ...
    def prev_vim_word(self) -> "Cursor": ...
    def end_of_vim_word(self) -> "Cursor": ...
    def next_word(self) -> "Cursor": ...
    def prev_word(self) -> "Cursor": ...
    def end_of_word(self) -> "Cursor": ...
    def start_of_logical_line(self) -> "Cursor": ...
    def first_non_blank_in_logical_line(self) -> "Cursor": ...
    def end_of_logical_line(self) -> "Cursor": ...
    def start_of_last_line(self) -> "Cursor": ...


def resolve_motion(key: str, cursor: Cursor, count: int) -> Cursor:
    """
    Resolve a motion to a target cursor position.
    Does not modify anything -- pure calculation.
    """
    result = cursor
    for _ in range(count):
        next_pos = _apply_single_motion(key, result)
        if next_pos.equals(result):
            break
        result = next_pos
    return result


def _apply_single_motion(key: str, cursor: Cursor) -> Cursor:
    """Apply a single motion step."""
    motion_map = {
        "h": cursor.left,
        "l": cursor.right,
        "j": cursor.down_logical_line,
        "k": cursor.up_logical_line,
        "gj": cursor.down,
        "gk": cursor.up,
        "w": cursor.next_vim_word,
        "b": cursor.prev_vim_word,
        "e": cursor.end_of_vim_word,
        "W": cursor.next_word,
        "B": cursor.prev_word,
        "E": cursor.end_of_word,
        "0": cursor.start_of_logical_line,
        "^": cursor.first_non_blank_in_logical_line,
        "$": cursor.end_of_logical_line,
        "G": cursor.start_of_last_line,
    }
    fn = motion_map.get(key)
    if fn is not None:
        return fn()
    return cursor


def is_inclusive_motion(key: str) -> bool:
    """Check if a motion is inclusive (includes character at destination)."""
    return key in "eE$"


def is_linewise_motion(key: str) -> bool:
    """
    Check if a motion is linewise (operates on full lines when used with operators).
    Note: gj/gk are characterwise exclusive per :help gj, not linewise.
    """
    return key in "jkG" or key == "gg"
