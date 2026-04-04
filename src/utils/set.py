"""
Set operation utilities -- optimized for performance.
"""

from __future__ import annotations

from typing import AbstractSet, TypeVar

A = TypeVar("A")


def difference(a: set[A], b: set[A]) -> set[A]:
    """Return elements in a that are not in b."""
    return a - b


def intersects(a: set[A], b: set[A]) -> bool:
    """Check if two sets have any elements in common."""
    if not a or not b:
        return False
    return not a.isdisjoint(b)


def every(a: AbstractSet[A], b: AbstractSet[A]) -> bool:
    """Check if every element of a is in b (a is subset of b)."""
    return a <= b


def union(a: set[A], b: set[A]) -> set[A]:
    """Return the union of two sets."""
    return a | b
