"""Text selection state for fullscreen mode.

Tracks a linear selection in screen-buffer coordinates (0-indexed col/row).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Point:
    col: int = 0
    row: int = 0


@dataclass
class AnchorSpan:
    lo: Point = field(default_factory=Point)
    hi: Point = field(default_factory=Point)
    kind: str = "word"  # 'word' | 'line'


@dataclass
class SelectionState:
    """Selection state for text selection in fullscreen mode."""
    anchor: Point | None = None
    focus: Point | None = None
    is_dragging: bool = False
    anchor_span: AnchorSpan | None = None
    scrolled_off_above: list[str] = field(default_factory=list)
    scrolled_off_below: list[str] = field(default_factory=list)
    scrolled_off_above_sw: list[bool] = field(default_factory=list)
    scrolled_off_below_sw: list[bool] = field(default_factory=list)
    virtual_anchor_row: int | None = None
    virtual_focus_row: int | None = None
    last_press_had_alt: bool = False


def create_selection_state() -> SelectionState:
    return SelectionState()


def start_selection(s: SelectionState, col: int, row: int) -> None:
    s.anchor = Point(col, row)
    s.focus = None
    s.is_dragging = True
    s.anchor_span = None
    s.scrolled_off_above = []
    s.scrolled_off_below = []
    s.scrolled_off_above_sw = []
    s.scrolled_off_below_sw = []
    s.virtual_anchor_row = None
    s.virtual_focus_row = None
    s.last_press_had_alt = False


def update_selection(s: SelectionState, col: int, row: int) -> None:
    if not s.is_dragging:
        return
    if not s.focus and s.anchor and s.anchor.col == col and s.anchor.row == row:
        return
    s.focus = Point(col, row)


def finish_selection(s: SelectionState) -> None:
    s.is_dragging = False


def clear_selection(s: SelectionState) -> None:
    s.anchor = None
    s.focus = None
    s.is_dragging = False
    s.anchor_span = None
    s.scrolled_off_above = []
    s.scrolled_off_below = []
    s.scrolled_off_above_sw = []
    s.scrolled_off_below_sw = []
    s.virtual_anchor_row = None
    s.virtual_focus_row = None
    s.last_press_had_alt = False


def has_selection(s: SelectionState) -> bool:
    return s.anchor is not None and s.focus is not None


def compare_points(a: Point, b: Point) -> int:
    if a.row != b.row:
        return -1 if a.row < b.row else 1
    if a.col != b.col:
        return -1 if a.col < b.col else 1
    return 0
