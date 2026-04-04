"""State machine for gating message writes during an initial flush."""

from __future__ import annotations

from typing import Generic, TypeVar

T = TypeVar("T")


class FlushGate(Generic[T]):
    """Gate message writes during an initial flush.

    Lifecycle:
      start() -> enqueue() returns True, items are queued
      end()   -> returns queued items for draining, enqueue() returns False
      drop()  -> discards queued items (permanent transport close)
      deactivate() -> clears active flag without dropping items
    """

    def __init__(self) -> None:
        self._active = False
        self._pending: list[T] = []

    @property
    def active(self) -> bool:
        return self._active

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def start(self) -> None:
        """Mark flush as in-progress."""
        self._active = True

    def end(self) -> list[T]:
        """End the flush and return any queued items for draining."""
        self._active = False
        items = list(self._pending)
        self._pending.clear()
        return items

    def enqueue(self, *items: T) -> bool:
        """If flush is active, queue items and return True. Otherwise return False."""
        if not self._active:
            return False
        self._pending.extend(items)
        return True

    def drop(self) -> int:
        """Discard all queued items. Returns the number of items dropped."""
        self._active = False
        count = len(self._pending)
        self._pending.clear()
        return count

    def deactivate(self) -> None:
        """Clear the active flag without dropping queued items."""
        self._active = False
