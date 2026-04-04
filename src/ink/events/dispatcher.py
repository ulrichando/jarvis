"""Event dispatcher with capture/bubble phases.

Owns event dispatch state and the capture/bubble dispatch loop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from .event_handlers import HANDLER_FOR_EVENT
from .terminal_event import EventTarget, TerminalEvent

logger = logging.getLogger(__name__)

# Event priorities (matching React reconciler constants)
DISCRETE_EVENT_PRIORITY = 1
CONTINUOUS_EVENT_PRIORITY = 4
DEFAULT_EVENT_PRIORITY = 16
NO_EVENT_PRIORITY = 0


@dataclass
class DispatchListener:
    node: Any  # EventTarget
    handler: Callable[[TerminalEvent], None]
    phase: str  # 'capturing' | 'at_target' | 'bubbling'


def _get_handler(
    node: Any, event_type: str, capture: bool
) -> Callable[[TerminalEvent], None] | None:
    """Get event handler from node for given event type and phase."""
    handlers = getattr(node, "_event_handlers", None)
    if not handlers:
        return None

    mapping = HANDLER_FOR_EVENT.get(event_type)
    if not mapping:
        return None

    prop_name = mapping.capture if capture else mapping.bubble
    if not prop_name:
        return None

    return handlers.get(prop_name)


def _collect_listeners(
    target: Any, event: TerminalEvent
) -> list[DispatchListener]:
    """Collect all listeners in dispatch order (capture root-first, bubble target-first)."""
    listeners: list[DispatchListener] = []

    node = target
    while node is not None:
        is_target = node is target

        capture_handler = _get_handler(node, event.type, True)
        bubble_handler = _get_handler(node, event.type, False)

        if capture_handler:
            listeners.insert(
                0,
                DispatchListener(
                    node=node,
                    handler=capture_handler,
                    phase="at_target" if is_target else "capturing",
                ),
            )

        if bubble_handler and (event.bubbles or is_target):
            listeners.append(
                DispatchListener(
                    node=node,
                    handler=bubble_handler,
                    phase="at_target" if is_target else "bubbling",
                )
            )

        node = getattr(node, "parent_node", None)

    return listeners


def _process_dispatch_queue(
    listeners: list[DispatchListener], event: TerminalEvent
) -> None:
    """Execute collected listeners with propagation control."""
    previous_node = None

    for listener in listeners:
        if event._is_immediate_propagation_stopped():
            break
        if event._is_propagation_stopped() and listener.node is not previous_node:
            break

        event._set_event_phase(listener.phase)
        event._set_current_target(listener.node)
        event._prepare_for_target(listener.node)

        try:
            listener.handler(event)
        except Exception:
            logger.exception("Error in event handler")

        previous_node = listener.node


def _get_event_priority(event_type: str) -> int:
    """Map terminal event types to scheduling priorities."""
    if event_type in ("keydown", "keyup", "click", "focus", "blur", "paste"):
        return DISCRETE_EVENT_PRIORITY
    if event_type in ("resize", "scroll", "mousemove"):
        return CONTINUOUS_EVENT_PRIORITY
    return DEFAULT_EVENT_PRIORITY


class Dispatcher:
    """Owns event dispatch state and the capture/bubble dispatch loop."""

    def __init__(self) -> None:
        self.current_event: TerminalEvent | None = None
        self.current_update_priority: int = DEFAULT_EVENT_PRIORITY
        self.discrete_updates: Callable | None = None

    def resolve_event_priority(self) -> int:
        """Infer event priority from the currently-dispatching event."""
        if self.current_update_priority != NO_EVENT_PRIORITY:
            return self.current_update_priority
        if self.current_event:
            return _get_event_priority(self.current_event.type)
        return DEFAULT_EVENT_PRIORITY

    def dispatch(self, target: Any, event: TerminalEvent) -> bool:
        """Dispatch an event through capture and bubble phases.
        Returns True if preventDefault() was NOT called.
        """
        previous_event = self.current_event
        self.current_event = event
        try:
            event._set_target(target)
            listeners = _collect_listeners(target, event)
            _process_dispatch_queue(listeners, event)
            event._set_event_phase("none")
            event._set_current_target(None)
            return not event.default_prevented
        finally:
            self.current_event = previous_event

    def dispatch_discrete(self, target: Any, event: TerminalEvent) -> bool:
        """Dispatch with discrete (sync) priority."""
        if not self.discrete_updates:
            return self.dispatch(target, event)
        return self.discrete_updates(
            lambda t, e: self.dispatch(t, e), target, event, None, None
        )

    def dispatch_continuous(self, target: Any, event: TerminalEvent) -> bool:
        """Dispatch with continuous priority."""
        previous_priority = self.current_update_priority
        try:
            self.current_update_priority = CONTINUOUS_EVENT_PRIORITY
            return self.dispatch(target, event)
        finally:
            self.current_update_priority = previous_priority
