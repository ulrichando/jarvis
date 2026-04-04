"""
Lazy schema construction utility.

Returns a memoized factory function that constructs the value on first call.
Used to defer schema construction from module init time to first access.
"""

from __future__ import annotations

from typing import Callable, TypeVar

T = TypeVar("T")


def lazy_schema(factory: Callable[[], T]) -> Callable[[], T]:
    """
    Create a lazy factory that constructs the value on first call
    and caches it for subsequent calls.

    Args:
        factory: A callable that produces the value to cache.

    Returns:
        A callable that returns the cached value, constructing it
        on first invocation.
    """
    cached: list[T] = []  # Use list to allow mutation in closure

    def getter() -> T:
        if not cached:
            cached.append(factory())
        return cached[0]

    return getter
