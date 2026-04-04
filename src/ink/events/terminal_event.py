"""Base class for all terminal events with DOM-style propagation.

Mirrors the browser's Event API: target, currentTarget, eventPhase,
stopPropagation(), preventDefault(), timeStamp.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from .event import Event

EventPhase = Literal["none", "capturing", "at_target", "bubbling"]


class EventTarget(Protocol):
    """Minimal event target interface."""

    parent_node: EventTarget | None
    _event_handlers: dict[str, Any] | None


@dataclass
class TerminalEventInit:
    bubbles: bool = True
    cancelable: bool = True


class TerminalEvent(Event):
    """Base class for all terminal events with DOM-style propagation."""

    def __init__(self, type_: str, init: TerminalEventInit | None = None) -> None:
        super().__init__()
        self.type: str = type_
        self.time_stamp: float = time.monotonic()
        self.bubbles: bool = init.bubbles if init else True
        self.cancelable: bool = init.cancelable if init else True

        self._target: Any | None = None
        self._current_target: Any | None = None
        self._event_phase: EventPhase = "none"
        self._propagation_stopped: bool = False
        self._default_prevented: bool = False

    @property
    def target(self) -> Any | None:
        return self._target

    @property
    def current_target(self) -> Any | None:
        return self._current_target

    @property
    def event_phase(self) -> EventPhase:
        return self._event_phase

    @property
    def default_prevented(self) -> bool:
        return self._default_prevented

    def stop_propagation(self) -> None:
        self._propagation_stopped = True

    def stop_immediate_propagation(self) -> None:
        super().stop_immediate_propagation()
        self._propagation_stopped = True

    def prevent_default(self) -> None:
        if self.cancelable:
            self._default_prevented = True

    # Internal setters used by the Dispatcher

    def _set_target(self, target: Any) -> None:
        self._target = target

    def _set_current_target(self, target: Any | None) -> None:
        self._current_target = target

    def _set_event_phase(self, phase: EventPhase) -> None:
        self._event_phase = phase

    def _is_propagation_stopped(self) -> bool:
        return self._propagation_stopped

    def _is_immediate_propagation_stopped(self) -> bool:
        return self.did_stop_immediate_propagation()

    def _prepare_for_target(self, target: Any) -> None:
        """Hook for subclasses to do per-node setup before each handler fires."""
        pass
