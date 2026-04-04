"""Yoga layout engine enums (flexbox layout constants)."""

from __future__ import annotations

from enum import IntEnum


class FlexDirection(IntEnum):
    COLUMN = 0
    COLUMN_REVERSE = 1
    ROW = 2
    ROW_REVERSE = 3


class JustifyContent(IntEnum):
    FLEX_START = 0
    CENTER = 1
    FLEX_END = 2
    SPACE_BETWEEN = 3
    SPACE_AROUND = 4
    SPACE_EVENLY = 5


class AlignItems(IntEnum):
    AUTO = 0
    FLEX_START = 1
    CENTER = 2
    FLEX_END = 3
    STRETCH = 4
    BASELINE = 5


class AlignSelf(IntEnum):
    AUTO = 0
    FLEX_START = 1
    CENTER = 2
    FLEX_END = 3
    STRETCH = 4
    BASELINE = 5


class FlexWrap(IntEnum):
    NO_WRAP = 0
    WRAP = 1
    WRAP_REVERSE = 2


class Overflow(IntEnum):
    VISIBLE = 0
    HIDDEN = 1
    SCROLL = 2


class Display(IntEnum):
    FLEX = 0
    NONE = 1


class PositionType(IntEnum):
    STATIC = 0
    RELATIVE = 1
    ABSOLUTE = 2


class Edge(IntEnum):
    LEFT = 0
    TOP = 1
    RIGHT = 2
    BOTTOM = 3
    START = 4
    END = 5
    HORIZONTAL = 6
    VERTICAL = 7
    ALL = 8
