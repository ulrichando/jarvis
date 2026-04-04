"""Geometry types and utilities for layout calculations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Point:
    x: int = 0
    y: int = 0


@dataclass
class Size:
    width: int = 0
    height: int = 0


@dataclass
class Rectangle:
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0


@dataclass
class Edges:
    top: int = 0
    right: int = 0
    bottom: int = 0
    left: int = 0


def edges(a: int, b: int | None = None, c: int | None = None, d: int | None = None) -> Edges:
    """Create edge insets."""
    if b is None:
        return Edges(top=a, right=a, bottom=a, left=a)
    if c is None:
        return Edges(top=a, right=b, bottom=a, left=b)
    return Edges(top=a, right=b, bottom=c, left=d if d is not None else 0)


def add_edges(a: Edges, b: Edges) -> Edges:
    return Edges(
        top=a.top + b.top, right=a.right + b.right,
        bottom=a.bottom + b.bottom, left=a.left + b.left,
    )


ZERO_EDGES = Edges()


def resolve_edges(partial: dict[str, int] | None = None) -> Edges:
    if not partial:
        return Edges()
    return Edges(
        top=partial.get("top", 0), right=partial.get("right", 0),
        bottom=partial.get("bottom", 0), left=partial.get("left", 0),
    )


def union_rect(a: Rectangle, b: Rectangle) -> Rectangle:
    min_x = min(a.x, b.x)
    min_y = min(a.y, b.y)
    max_x = max(a.x + a.width, b.x + b.width)
    max_y = max(a.y + a.height, b.y + b.height)
    return Rectangle(x=min_x, y=min_y, width=max_x - min_x, height=max_y - min_y)


def clamp_rect(rect: Rectangle, size: Size) -> Rectangle:
    min_x = max(0, rect.x)
    min_y = max(0, rect.y)
    max_x = min(size.width - 1, rect.x + rect.width - 1)
    max_y = min(size.height - 1, rect.y + rect.height - 1)
    return Rectangle(x=min_x, y=min_y, width=max(0, max_x - min_x + 1), height=max(0, max_y - min_y + 1))


def within_bounds(size: Size, point: Point) -> bool:
    return 0 <= point.x < size.width and 0 <= point.y < size.height


def clamp(value: int | float, min_val: int | float | None = None, max_val: int | float | None = None) -> int | float:
    if min_val is not None and value < min_val:
        return min_val
    if max_val is not None and value > max_val:
        return max_val
    return value
