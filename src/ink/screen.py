"""Screen buffer for terminal rendering.

The screen is a 2D grid of cells, each with a character, style, and width.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable


class CellWidth(IntEnum):
    """Cell width types."""
    Normal = 0
    Wide = 1
    SpacerTail = 2   # Second cell of a wide char
    SpacerHead = 3   # Line-end padding for wide char wrap


@dataclass
class Cell:
    """A single cell in the screen buffer."""
    char: str = " "
    style_id: int = 0
    width: int = 0  # CellWidth
    hyperlink: str | None = None


Hyperlink = str | None


@dataclass
class Screen:
    """Screen buffer."""
    width: int = 0
    height: int = 0
    cells: list[int] = field(default_factory=list)
    char_pool: list[str] = field(default_factory=list)
    hyperlink_pool: list[str | None] = field(default_factory=list)
    no_select: list[int] = field(default_factory=list)
    soft_wrap: list[bool] = field(default_factory=list)
    damage: Any = None


class StylePool:
    """Pool for style deduplication."""

    def __init__(self) -> None:
        self.none: int = 0
        self._styles: list[list] = [[]]  # style 0 = no style
        self._transitions: dict[tuple[int, int], str] = {}

    def get(self, style_id: int) -> list:
        if style_id < len(self._styles):
            return self._styles[style_id]
        return []

    def transition(self, from_id: int, to_id: int) -> str:
        key = (from_id, to_id)
        cached = self._transitions.get(key)
        if cached is not None:
            return cached
        # Simple: just return reset if different
        result = "" if from_id == to_id else "\033[0m"
        self._transitions[key] = result
        return result

    def with_inverse(self, style_id: int) -> int:
        return style_id  # Simplified


class CharPool:
    """Pool for character string deduplication."""
    pass


class HyperlinkPool:
    """Pool for hyperlink string deduplication."""
    pass


def create_screen(
    width: int, height: int,
    style_pool: Any = None, char_pool: Any = None, hyperlink_pool: Any = None
) -> Screen:
    """Create a new screen buffer."""
    total = width * height
    return Screen(
        width=width,
        height=height,
        cells=[0] * (total * 2),  # packed cell data
        char_pool=[" "] * total,
        hyperlink_pool=[None] * total,
        no_select=[0] * total,
        soft_wrap=[False] * height,
    )


def cell_at(screen: Screen, x: int, y: int) -> Cell | None:
    """Get the cell at (x, y)."""
    if x < 0 or x >= screen.width or y < 0 or y >= screen.height:
        return None
    idx = y * screen.width + x
    return Cell(
        char=screen.char_pool[idx] if idx < len(screen.char_pool) else " ",
        style_id=0,
        width=CellWidth.Normal,
    )


def cell_at_index(screen: Screen, idx: int) -> Cell:
    """Get the cell at a flat index."""
    return Cell(
        char=screen.char_pool[idx] if idx < len(screen.char_pool) else " ",
        style_id=0,
        width=CellWidth.Normal,
    )


def char_in_cell_at(screen: Screen, x: int, y: int) -> str | None:
    """Get just the character at (x, y)."""
    idx = y * screen.width + x
    if 0 <= idx < len(screen.char_pool):
        return screen.char_pool[idx]
    return None


def set_cell_style_id(screen: Screen, x: int, y: int, style_id: int) -> None:
    """Set the style ID for a cell."""
    pass  # Simplified


def is_empty_cell_at(screen: Screen, x: int, y: int) -> bool:
    """Check if a cell is empty."""
    idx = y * screen.width + x
    if 0 <= idx < len(screen.char_pool):
        return screen.char_pool[idx] == " "
    return True


def diff_each(
    prev: Screen, next_: Screen,
    callback: Callable[[int, int, Cell | None, Cell | None], bool | None]
) -> None:
    """Call callback for each cell that differs between screens."""
    max_h = max(prev.height, next_.height)
    max_w = max(prev.width, next_.width)
    for y in range(max_h):
        for x in range(max_w):
            prev_cell = cell_at(prev, x, y)
            next_cell = cell_at(next_, x, y)
            # Simplified comparison
            if prev_cell != next_cell:
                result = callback(x, y, prev_cell, next_cell)
                if result:
                    return


def visible_cell_at_index(
    cells: list[int], char_pool: list[str], hyperlink_pool: list,
    index: int, last_rendered_style_id: int
) -> Cell | None:
    """Get visible cell at index, skipping invisible cells."""
    if index >= len(char_pool):
        return None
    char = char_pool[index]
    if char == " ":
        return None
    return Cell(char=char)


def shift_rows(screen: Screen, top: int, bottom: int, delta: int) -> None:
    """Shift rows in the screen buffer for scroll optimization."""
    pass  # Simplified
