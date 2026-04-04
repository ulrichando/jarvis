"""
Object.groupBy polyfill -- group items by a key selector.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable, Hashable, Iterable, TypeVar

T = TypeVar("T")
K = TypeVar("K", bound=Hashable)


def object_group_by(
    items: Iterable[T], key_selector: Callable[[T, int], K]
) -> dict[K, list[T]]:
    """
    Group items by a key selector function.

    Args:
        items: Iterable of items to group.
        key_selector: Function that takes (item, index) and returns a key.

    Returns:
        Dict mapping keys to lists of items.
    """
    result: dict[K, list[T]] = defaultdict(list)
    for index, item in enumerate(items):
        key = key_selector(item, index)
        result[key].append(item)
    return dict(result)
