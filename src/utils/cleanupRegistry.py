"""
Global registry for cleanup functions that should run during graceful shutdown.
Separate from graceful_shutdown to avoid circular dependencies.
"""

from __future__ import annotations

import asyncio
from typing import Callable, Awaitable

# Global registry for cleanup functions
_cleanup_functions: set[Callable[[], Awaitable[None]]] = set()


def register_cleanup(cleanup_fn: Callable[[], Awaitable[None]]) -> Callable[[], None]:
    """
    Register a cleanup function to run during graceful shutdown.

    Args:
        cleanup_fn: Async function to run during cleanup.

    Returns:
        Unregister function that removes the cleanup handler.
    """
    _cleanup_functions.add(cleanup_fn)

    def unregister() -> None:
        _cleanup_functions.discard(cleanup_fn)

    return unregister


async def run_cleanup_functions() -> None:
    """Run all registered cleanup functions."""
    await asyncio.gather(*(fn() for fn in _cleanup_functions), return_exceptions=True)
