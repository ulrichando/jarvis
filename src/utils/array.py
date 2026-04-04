"""
Array utility functions.
"""

from __future__ import annotations

from typing import Callable, Iterable, TypeVar

T = TypeVar("T")
A = TypeVar("A")


def intersperse(items: list[A], separator: Callable[[int], A]) -> list[A]:
    """Insert separator elements between items in a list."""
    result: list[A] = []
    for i, item in enumerate(items):
        if i > 0:
            result.append(separator(i))
        result.append(item)
    return result


def count(arr: Iterable[T], pred: Callable[[T], object]) -> int:
    """Count items in an iterable that satisfy a predicate."""
    return sum(1 for x in arr if pred(x))


def uniq(xs: Iterable[T]) -> list[T]:
    """Return unique items preserving order."""
    seen: set[T] = set()
    result: list[T] = []
    for x in xs:
        if x not in seen:
            seen.add(x)
            result.append(x)
    return result
