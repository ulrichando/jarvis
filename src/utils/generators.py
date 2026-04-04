"""
Async generator utilities.
"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator, TypeVar

T = TypeVar("T")


async def last_x(gen: AsyncGenerator[T, None]) -> T:
    """Get the last value from an async generator."""
    last_value = None
    found = False
    async for value in gen:
        last_value = value
        found = True
    if not found:
        raise ValueError("No items in generator")
    return last_value  # type: ignore


async def to_array(gen: AsyncGenerator[T, None]) -> list[T]:
    """Collect all values from an async generator into a list."""
    result: list[T] = []
    async for value in gen:
        result.append(value)
    return result


async def from_array(values: list[T]) -> AsyncGenerator[T, None]:
    """Create an async generator from a list."""
    for value in values:
        yield value


async def all_generators(
    generators: list[AsyncGenerator[T, None]],
    concurrency_cap: int = 0,
) -> AsyncGenerator[T, None]:
    """
    Run all generators concurrently up to a concurrency cap,
    yielding values as they come in.
    """
    if concurrency_cap <= 0:
        concurrency_cap = len(generators)

    queue: asyncio.Queue[tuple[bool, T | None]] = asyncio.Queue()
    active = 0
    waiting = list(generators)

    async def consume(gen: AsyncGenerator[T, None]) -> None:
        nonlocal active
        try:
            async for value in gen:
                await queue.put((False, value))
        finally:
            active -= 1
            await queue.put((True, None))

    # Start initial batch
    tasks: list[asyncio.Task[None]] = []
    while active < concurrency_cap and waiting:
        gen = waiting.pop(0)
        active += 1
        tasks.append(asyncio.create_task(consume(gen)))

    while active > 0 or not queue.empty():
        done, value = await queue.get()
        if done:
            # Start next generator if available
            if waiting:
                gen = waiting.pop(0)
                active += 1
                tasks.append(asyncio.create_task(consume(gen)))
        else:
            if value is not None:
                yield value
