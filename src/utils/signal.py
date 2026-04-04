"""
Tiny listener-set primitive for pure event signals (no stored state).

Usage:
    changed = create_signal()
    unsubscribe = changed.subscribe(lambda: print("changed!"))
    changed.emit()
"""

from __future__ import annotations

from typing import Any, Callable, Generic, TypeVar


class Signal:
    """Simple event signal with subscribe/emit/clear."""

    def __init__(self) -> None:
        self._listeners: set[Callable[..., None]] = set()

    def subscribe(self, listener: Callable[..., None]) -> Callable[[], None]:
        """Subscribe a listener. Returns an unsubscribe function."""
        self._listeners.add(listener)

        def unsubscribe() -> None:
            self._listeners.discard(listener)

        return unsubscribe

    def emit(self, *args: Any) -> None:
        """Call all subscribed listeners with the given arguments."""
        for listener in list(self._listeners):
            listener(*args)

    def clear(self) -> None:
        """Remove all listeners."""
        self._listeners.clear()


def create_signal() -> Signal:
    """Create a new Signal instance."""
    return Signal()
