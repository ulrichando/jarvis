"""Yoga layout engine adapter.

Provides a LayoutNode implementation backed by Yoga (or a simple fallback).
"""

from __future__ import annotations

from typing import Any, Callable

from .node import LayoutNode


class YogaLayoutNode(LayoutNode):
    """Simple layout node implementation (Yoga-like)."""

    def __init__(self) -> None:
        self._width: float = 0
        self._height: float = 0
        self._top: float = 0
        self._left: float = 0
        self._right: float = 0
        self._bottom: float = 0
        self._children: list[YogaLayoutNode] = []
        self._measure_func: Callable | None = None
        self._display: str = "flex"
        self._padding: dict[str, float] = {}
        self._border: dict[str, float] = {}
        self._margin: dict[str, float] = {}
        self._dirty: bool = True

    def set_width(self, width: float) -> None: self._width = width
    def set_height(self, height: float) -> None: self._height = height
    def set_width_auto(self) -> None: self._width = 0
    def set_height_auto(self) -> None: self._height = 0
    def set_width_percent(self, percent: float) -> None: pass
    def set_height_percent(self, percent: float) -> None: pass
    def set_min_width(self, width: float) -> None: pass
    def set_min_height(self, height: float) -> None: pass
    def set_max_width(self, width: float) -> None: pass
    def set_max_height(self, height: float) -> None: pass
    def set_min_width_percent(self, percent: float) -> None: pass
    def set_min_height_percent(self, percent: float) -> None: pass
    def set_max_width_percent(self, percent: float) -> None: pass
    def set_max_height_percent(self, percent: float) -> None: pass
    def set_flex_grow(self, grow: float) -> None: pass
    def set_flex_shrink(self, shrink: float) -> None: pass
    def set_flex_basis(self, basis: float) -> None: pass
    def set_flex_basis_percent(self, percent: float) -> None: pass
    def set_flex_direction(self, direction: str) -> None: pass
    def set_flex_wrap(self, wrap: str) -> None: pass
    def set_align_items(self, align: str) -> None: pass
    def set_align_self(self, align: str) -> None: pass
    def set_justify_content(self, justify: str) -> None: pass
    def set_display(self, display: str) -> None: self._display = display
    def set_overflow(self, overflow: str) -> None: pass
    def set_position_type(self, position: str) -> None: pass
    def set_position(self, edge: str, value: float) -> None: pass
    def set_position_percent(self, edge: str, percent: float) -> None: pass
    def set_margin(self, edge: str, value: float) -> None: self._margin[edge] = value
    def set_padding(self, edge: str, value: float) -> None: self._padding[edge] = value
    def set_border(self, edge: str, value: float) -> None: self._border[edge] = value
    def set_gap(self, gutter: str, value: float) -> None: pass
    def set_measure_func(self, func: Callable) -> None: self._measure_func = func
    def mark_dirty(self) -> None: self._dirty = True

    def insert_child(self, child: LayoutNode, index: int) -> None:
        if isinstance(child, YogaLayoutNode):
            self._children.insert(index, child)

    def remove_child(self, child: LayoutNode) -> None:
        if isinstance(child, YogaLayoutNode) and child in self._children:
            self._children.remove(child)

    def get_child_count(self) -> int: return len(self._children)

    def calculate_layout(self, width: float, height: float) -> None:
        self._width = width
        self._height = height

    def get_computed_width(self) -> float: return self._width
    def get_computed_height(self) -> float: return self._height
    def get_computed_top(self) -> float: return self._top
    def get_computed_left(self) -> float: return self._left
    def get_computed_right(self) -> float: return self._right
    def get_computed_bottom(self) -> float: return self._bottom
    def get_computed_padding(self, edge: str) -> float: return self._padding.get(edge, 0)
    def get_computed_border(self, edge: str) -> float: return self._border.get(edge, 0)
    def get_computed_margin(self, edge: str) -> float: return self._margin.get(edge, 0)
    def get_display(self) -> str: return self._display
    def free(self) -> None: pass
    def free_recursive(self) -> None: self._children.clear()


def create_yoga_layout_node() -> YogaLayoutNode:
    """Create a new Yoga layout node."""
    return YogaLayoutNode()
