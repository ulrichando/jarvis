"""Command queue state management.

Provides a reactive command queue that notifies subscribers on changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List


@dataclass
class QueuedCommand:
    """A command queued for execution."""

    command: str
    args: dict = None

    def __post_init__(self):
        if self.args is None:
            self.args = {}


class CommandQueue:
    """Observable command queue.

    Equivalent to useCommandQueue React hook using useSyncExternalStore.
    """

    def __init__(self):
        self._queue: List[QueuedCommand] = []
        self._subscribers: List[Callable[[], None]] = []

    @property
    def commands(self) -> tuple[QueuedCommand, ...]:
        """Return immutable snapshot of current queue."""
        return tuple(self._queue)

    def __len__(self) -> int:
        return len(self._queue)

    def subscribe(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Subscribe to queue changes. Returns unsubscribe function."""
        self._subscribers.append(callback)

        def unsubscribe():
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return unsubscribe

    def _notify(self) -> None:
        for cb in self._subscribers:
            cb()

    def enqueue(self, command: QueuedCommand) -> None:
        self._queue.append(command)
        self._notify()

    def dequeue(self) -> QueuedCommand | None:
        if not self._queue:
            return None
        cmd = self._queue.pop(0)
        self._notify()
        return cmd

    def clear(self) -> None:
        if self._queue:
            self._queue.clear()
            self._notify()

    def has_commands(self) -> bool:
        return len(self._queue) > 0
