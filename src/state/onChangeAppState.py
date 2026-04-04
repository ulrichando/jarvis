"""App state change observers."""

from __future__ import annotations

from typing import Any, Callable


_observers: list[Callable[[dict[str, Any]], None]] = []


def on_change_app_state(callback: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
    """Register an observer for app state changes. Returns unsubscribe function."""
    _observers.append(callback)
    def unsubscribe():
        _observers.remove(callback)
    return unsubscribe


def notify_state_change(state: dict[str, Any]) -> None:
    """Notify all observers of a state change."""
    for observer in _observers:
        observer(state)
