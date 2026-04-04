"""Yoga layout engine Python bindings (simplified)."""

from __future__ import annotations

from .enums import *


class YogaNode:
    """Simplified Yoga layout node."""

    def __init__(self) -> None:
        self._children: list[YogaNode] = []
        self._width: float = 0
        self._height: float = 0
        self._flex_direction = FlexDirection.COLUMN
        self._justify_content = JustifyContent.FLEX_START

    def set_width(self, width: float) -> None:
        self._width = width

    def set_height(self, height: float) -> None:
        self._height = height

    def set_flex_direction(self, direction: FlexDirection) -> None:
        self._flex_direction = direction

    def insert_child(self, child: "YogaNode", index: int) -> None:
        self._children.insert(index, child)

    def calculate_layout(self) -> None:
        pass

    def get_computed_width(self) -> float:
        return self._width

    def get_computed_height(self) -> float:
        return self._height
