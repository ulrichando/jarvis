"""TodoV2 task list management with file watching."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Dict, List, Optional

HIDE_DELAY_MS = 5000
DEBOUNCE_MS = 0.05  # seconds
FALLBACK_POLL_MS = 5.0  # seconds


class TasksV2Store:
    """Singleton store for the TodoV2 task list.

    Owns the file watcher, timers, and cached task list.
    Multiple consumers subscribe to one shared store.

    Equivalent to TasksV2Store class + useTasksV2 React hook.
    """

    def __init__(self):
        self._tasks: Optional[List[Any]] = None
        self._hidden = False
        self._subscribers: List[Callable] = []
        self._started = False
        self._hide_timer: Optional[float] = None

    @property
    def snapshot(self) -> Optional[List[Any]]:
        """Get the current task list snapshot. None when hidden."""
        return None if self._hidden else self._tasks

    def subscribe(self, callback: Callable) -> Callable:
        """Subscribe to task list changes. Returns unsubscribe function."""
        self._subscribers.append(callback)

        def unsubscribe():
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return unsubscribe

    def _notify(self) -> None:
        for cb in self._subscribers:
            cb()

    async def fetch(self, list_tasks: Callable, task_list_id: str) -> None:
        """Fetch current tasks and update state."""
        tasks = await list_tasks(task_list_id)
        # Filter internal tasks
        self._tasks = [t for t in tasks if not (t.get("metadata", {}) or {}).get("_internal")]

        has_incomplete = any(t.get("status") != "completed" for t in self._tasks)

        if has_incomplete or not self._tasks:
            self._hidden = not self._tasks
            self._hide_timer = None
        elif self._hide_timer is None and not self._hidden:
            # All tasks completed - schedule hide
            self._hide_timer = time.time() + HIDE_DELAY_MS / 1000

        self._notify()

    def check_hide_timer(self) -> None:
        """Check if the hide timer has elapsed."""
        if self._hide_timer and time.time() >= self._hide_timer:
            self._hide_timer = None
            if self._tasks and all(
                t.get("status") == "completed" for t in self._tasks
            ):
                self._tasks = []
                self._hidden = True
                self._notify()


# Module-level singleton
_store: Optional[TasksV2Store] = None


def get_tasks_v2_store() -> TasksV2Store:
    global _store
    if _store is None:
        _store = TasksV2Store()
    return _store
