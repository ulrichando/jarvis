"""Startup notification system."""

from __future__ import annotations

from typing import Any, Callable, Optional


async def run_startup_notification(
    check_fn: Callable,
    add_notification: Callable,
) -> None:
    """Run a startup notification check and add result if non-null.

    Equivalent to useStartupNotification React hook.
    """
    result = await check_fn()
    if result:
        add_notification(**result)
