"""
Sequential execution wrapper for async functions to prevent race conditions.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")


def sequential(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    """
    Creates a sequential execution wrapper for async functions.
    Ensures that concurrent calls are executed one at a time
    in the order they were received.
    """
    lock = asyncio.Lock()

    async def wrapper(*args: Any, **kwargs: Any) -> T:
        async with lock:
            return await fn(*args, **kwargs)

    return wrapper
