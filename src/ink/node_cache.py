"""Cached layout bounds for rendered nodes."""

from __future__ import annotations

import weakref
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CachedLayout:
    """Cached layout bounds for a rendered node."""
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    top: int | None = None


@dataclass
class Rectangle:
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0


# WeakValueDictionary would be ideal but we need to map FROM DOMElement
# Use a regular dict and manage cleanup manually
node_cache: dict[int, CachedLayout] = {}
pending_clears: dict[int, list[Rectangle]] = {}

_absolute_node_removed = False


def add_pending_clear(parent: Any, rect: CachedLayout, is_absolute: bool) -> None:
    """Add a pending clear rect for a parent node."""
    global _absolute_node_removed
    parent_id = id(parent)
    existing = pending_clears.get(parent_id)
    r = Rectangle(x=rect.x, y=rect.y, width=rect.width, height=rect.height)
    if existing:
        existing.append(r)
    else:
        pending_clears[parent_id] = [r]
    if is_absolute:
        _absolute_node_removed = True


def consume_absolute_removed_flag() -> bool:
    """Consume and return the absolute-node-removed flag."""
    global _absolute_node_removed
    had = _absolute_node_removed
    _absolute_node_removed = False
    return had
