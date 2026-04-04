"""
Async stream implementation with queue-based producer/consumer pattern.

Provides an async iterator that can be fed values from a producer
via enqueue(), and signals completion via done() or error().
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, Generic, Optional, TypeVar, Callable

T = TypeVar("T")


class Stream(Generic[T]):
    """
    An async iterator that bridges a producer (enqueue/done/error)
    to an async consumer (async for item in stream).

    Can only be iterated once.
    """

    def __init__(self, returned: Optional[Callable[[], None]] = None) -> None:
        self._queue: asyncio.Queue[tuple[bool, Optional[T], Optional[BaseException]]] = asyncio.Queue()
        self._is_done: bool = False
        self._has_error: Optional[BaseException] = None
        self._started: bool = False
        self._returned = returned

    def __aiter__(self) -> AsyncIterator[T]:
        if self._started:
            raise RuntimeError("Stream can only be iterated once")
        self._started = True
        return self

    async def __anext__(self) -> T:
        if self._is_done:
            raise StopAsyncIteration

        if self._has_error is not None:
            raise self._has_error

        done, value, error = await self._queue.get()

        if error is not None:
            self._has_error = error
            raise error

        if done:
            self._is_done = True
            raise StopAsyncIteration

        assert value is not None
        return value

    def enqueue(self, value: T) -> None:
        """Add a value to the stream."""
        self._queue.put_nowait((False, value, None))

    def done(self) -> None:
        """Signal that the stream is complete."""
        self._is_done = True
        self._queue.put_nowait((True, None, None))

    def error(self, err: BaseException) -> None:
        """Signal an error on the stream."""
        self._has_error = err
        self._queue.put_nowait((False, None, err))

    async def areturn(self) -> None:
        """Called when the consumer breaks out of iteration."""
        self._is_done = True
        if self._returned:
            self._returned()
