"""
Synchronous state machine for the query lifecycle.

Three states:
  idle        -- no query, safe to dequeue and process
  dispatching -- an item was dequeued, async chain hasn't reached on_query yet
  running     -- on_query called try_start(), query is executing

Transitions:
  idle -> dispatching  (reserve)
  dispatching -> running  (try_start)
  idle -> running  (try_start, for direct user submissions)
  running -> idle  (end / force_end)
  dispatching -> idle  (cancel_reservation, when process_queue_if_ready fails)

is_active returns True for both dispatching and running, preventing
re-entry from the queue processor during the async gap.
"""

from __future__ import annotations

from typing import Callable, List, Literal, Optional

_Status = Literal["idle", "dispatching", "running"]


class QueryGuard:
    """State machine guard for query lifecycle management."""

    def __init__(self) -> None:
        self._status: _Status = "idle"
        self._generation: int = 0
        self._listeners: List[Callable[[], None]] = []

    def reserve(self) -> bool:
        """
        Reserve the guard for queue processing. Transitions idle -> dispatching.
        Returns False if not idle.
        """
        if self._status != "idle":
            return False
        self._status = "dispatching"
        self._notify()
        return True

    def cancel_reservation(self) -> None:
        """
        Cancel a reservation when process_queue_if_ready had nothing to process.
        Transitions dispatching -> idle.
        """
        if self._status != "dispatching":
            return
        self._status = "idle"
        self._notify()

    def try_start(self) -> Optional[int]:
        """
        Start a query. Returns the generation number on success,
        or None if a query is already running.
        Accepts transitions from both idle and dispatching.
        """
        if self._status == "running":
            return None
        self._status = "running"
        self._generation += 1
        self._notify()
        return self._generation

    def end(self, generation: int) -> bool:
        """
        End a query. Returns True if this generation is still current.
        Returns False if a newer query has started.
        """
        if self._generation != generation:
            return False
        if self._status != "running":
            return False
        self._status = "idle"
        self._notify()
        return True

    def force_end(self) -> None:
        """
        Force-end the current query regardless of generation.
        Increments generation so stale finally blocks will see a mismatch.
        """
        if self._status == "idle":
            return
        self._status = "idle"
        self._generation += 1
        self._notify()

    @property
    def is_active(self) -> bool:
        """Is the guard active (dispatching or running)?"""
        return self._status != "idle"

    @property
    def generation(self) -> int:
        return self._generation

    def subscribe(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Subscribe to state changes. Returns an unsubscribe function."""
        self._listeners.append(listener)

        def unsubscribe() -> None:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

        return unsubscribe

    def get_snapshot(self) -> bool:
        """Returns is_active as a snapshot value."""
        return self._status != "idle"

    def _notify(self) -> None:
        for listener in self._listeners:
            listener()
