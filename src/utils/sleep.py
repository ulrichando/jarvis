"""
Abort-responsive sleep and timeout utilities.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional


async def sleep(
    ms: float,
    check_abort: Optional[Callable[[], bool]] = None,
    throw_on_abort: bool = False,
) -> None:
    """
    Abort-responsive sleep. Resolves after ms milliseconds, or immediately
    when abort is signaled.

    Args:
        ms: Milliseconds to sleep.
        check_abort: Optional callable that returns True if aborted.
        throw_on_abort: If True, raise on abort instead of returning.
    """
    if check_abort and check_abort():
        if throw_on_abort:
            raise asyncio.CancelledError("aborted")
        return

    try:
        await asyncio.sleep(ms / 1000)
    except asyncio.CancelledError:
        if throw_on_abort:
            raise
        return


async def with_timeout(
    coro: Any,
    ms: float,
    message: str = "Operation timed out",
) -> Any:
    """
    Race a coroutine against a timeout.
    Raises TimeoutError if the coroutine doesn't settle within ms.

    Args:
        coro: Coroutine to execute.
        ms: Timeout in milliseconds.
        message: Error message for timeout.
    """
    try:
        return await asyncio.wait_for(coro, timeout=ms / 1000)
    except asyncio.TimeoutError:
        raise TimeoutError(message)
