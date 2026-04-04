"""Event emitter that respects stopImmediatePropagation."""

from __future__ import annotations

from typing import Any, Callable

from .event import Event


class EventEmitter:
    """Event emitter aware of our Event class and stopImmediatePropagation."""

    def __init__(self) -> None:
        self._listeners: dict[str, list[Callable]] = {}

    def on(self, event_type: str, listener: Callable) -> None:
        """Add a listener for an event type."""
        if event_type not in self._listeners:
            self._listeners[event_type] = []
        self._listeners[event_type].append(listener)

    def off(self, event_type: str, listener: Callable) -> None:
        """Remove a listener for an event type."""
        if event_type in self._listeners:
            try:
                self._listeners[event_type].remove(listener)
            except ValueError:
                pass

    def emit(self, event_type: str, *args: Any) -> bool:
        """Emit an event, respecting stopImmediatePropagation."""
        listeners = self._listeners.get(event_type, [])
        if not listeners:
            return False

        cc_event = args[0] if args and isinstance(args[0], Event) else None

        for listener in list(listeners):
            listener(*args)
            if cc_event and cc_event.did_stop_immediate_propagation():
                break

        return True

    def remove_all_listeners(self, event_type: str | None = None) -> None:
        """Remove all listeners, optionally for a specific event type."""
        if event_type is None:
            self._listeners.clear()
        elif event_type in self._listeners:
            del self._listeners[event_type]
