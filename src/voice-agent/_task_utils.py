"""Small asyncio helpers. Leaf module — no project imports (avoids cycles)."""
from __future__ import annotations

import asyncio
import logging

_log = logging.getLogger(__name__)


def log_task_exception(task: "asyncio.Task") -> None:
    """done_callback that surfaces a fire-and-forget task's exception.

    Without this, an exception raised inside a bare ``create_task`` coroutine is
    swallowed (only a GC-time "Task exception was never retrieved" warning).
    Attach via ``task.add_done_callback(log_task_exception)``.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        _log.error(
            "background task %r failed: %s", task.get_name(), exc, exc_info=exc
        )
