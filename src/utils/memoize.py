"""
Memoization utilities with TTL and LRU eviction policies.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from typing import Any, Callable, Generic, Optional, TypeVar

T = TypeVar("T")


class MemoizeWithTTL(Generic[T]):
    """
    Memoized function that returns cached values while refreshing in parallel.
    Implements a write-through cache pattern.
    """

    def __init__(
        self,
        func: Callable[..., T],
        cache_lifetime_ms: float = 300_000,  # 5 minutes
    ) -> None:
        self._func = func
        self._cache_lifetime_ms = cache_lifetime_ms
        self._cache: dict[str, tuple[T, float, bool]] = {}  # value, timestamp, refreshing

    def __call__(self, *args: Any) -> T:
        key = json.dumps(args, default=str)
        now = time.time() * 1000

        if key not in self._cache:
            value = self._func(*args)
            self._cache[key] = (value, now, False)
            return value

        value, timestamp, refreshing = self._cache[key]
        if now - timestamp > self._cache_lifetime_ms and not refreshing:
            self._cache[key] = (value, timestamp, True)
            try:
                new_value = self._func(*args)
                self._cache[key] = (new_value, time.time() * 1000, False)
            except Exception:
                del self._cache[key]
            return value

        return self._cache[key][0]

    def clear(self) -> None:
        self._cache.clear()


class LRUMemoize(Generic[T]):
    """
    Memoized function with LRU (Least Recently Used) eviction policy.
    Prevents unbounded memory growth.
    """

    def __init__(
        self,
        func: Callable[..., T],
        cache_fn: Callable[..., str],
        max_cache_size: int = 100,
    ) -> None:
        self._func = func
        self._cache_fn = cache_fn
        self._max_size = max_cache_size
        self._cache: OrderedDict[str, T] = OrderedDict()

    def __call__(self, *args: Any) -> T:
        key = self._cache_fn(*args)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]

        result = self._func(*args)
        self._cache[key] = result
        if len(self._cache) > self._max_size:
            self._cache.popitem(last=False)
        return result

    def clear(self) -> None:
        self._cache.clear()

    def size(self) -> int:
        return len(self._cache)

    def delete(self, key: str) -> bool:
        if key in self._cache:
            del self._cache[key]
            return True
        return False

    def get(self, key: str) -> Optional[T]:
        return self._cache.get(key)

    def has(self, key: str) -> bool:
        return key in self._cache


def memoize_with_ttl(
    func: Callable[..., T], cache_lifetime_ms: float = 300_000
) -> MemoizeWithTTL[T]:
    """Create a memoized function with TTL-based cache expiration."""
    return MemoizeWithTTL(func, cache_lifetime_ms)


def memoize_with_lru(
    func: Callable[..., T],
    cache_fn: Callable[..., str],
    max_cache_size: int = 100,
) -> LRUMemoize[T]:
    """Create a memoized function with LRU eviction policy."""
    return LRUMemoize(func, cache_fn, max_cache_size)
