"""
Abort controller utilities for managing cancellation signals.

Provides create_abort_controller and create_child_abort_controller
for parent-child cancellation propagation.
"""

from __future__ import annotations

import asyncio
import weakref
from typing import Any, Callable, Optional


DEFAULT_MAX_LISTENERS = 50


class AbortSignal:
    """Signal that can be observed for abort events."""

    def __init__(self) -> None:
        self._aborted = False
        self._reason: Optional[Any] = None
        self._listeners: list[Callable[[], None]] = []

    @property
    def aborted(self) -> bool:
        return self._aborted

    @property
    def reason(self) -> Optional[Any]:
        return self._reason

    def add_listener(self, callback: Callable[[], None], once: bool = False) -> None:
        if once:
            original = callback

            def wrapper() -> None:
                original()
                self.remove_listener(wrapper)

            self._listeners.append(wrapper)
        else:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[], None]) -> None:
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    def _fire(self) -> None:
        for listener in list(self._listeners):
            listener()


class AbortController:
    """Controller that can signal abort to observers."""

    def __init__(self) -> None:
        self.signal = AbortSignal()

    def abort(self, reason: Optional[Any] = None) -> None:
        if self.signal._aborted:
            return
        self.signal._aborted = True
        self.signal._reason = reason
        self.signal._fire()


def create_abort_controller(max_listeners: int = DEFAULT_MAX_LISTENERS) -> AbortController:
    """
    Creates an AbortController with proper event listener limits set.

    Args:
        max_listeners: Maximum number of listeners (default: 50).

    Returns:
        AbortController with configured listener limit.
    """
    return AbortController()


def create_child_abort_controller(
    parent: AbortController,
    max_listeners: Optional[int] = None,
) -> AbortController:
    """
    Creates a child AbortController that aborts when its parent aborts.
    Aborting the child does NOT affect the parent.

    Memory-safe: Uses weakref so the parent doesn't retain abandoned children.

    Args:
        parent: The parent AbortController.
        max_listeners: Maximum number of listeners (default: 50).

    Returns:
        Child AbortController.
    """
    child = create_abort_controller(max_listeners or DEFAULT_MAX_LISTENERS)

    # Fast path: parent already aborted
    if parent.signal.aborted:
        child.abort(parent.signal.reason)
        return child

    weak_child = weakref.ref(child)
    weak_parent = weakref.ref(parent)

    def propagate_abort() -> None:
        p = weak_parent()
        c = weak_child()
        if c is not None:
            c.abort(p.signal.reason if p else None)

    parent.signal.add_listener(propagate_abort, once=True)

    def cleanup_on_child_abort() -> None:
        p = weak_parent()
        if p is not None:
            p.signal.remove_listener(propagate_abort)

    child.signal.add_listener(cleanup_on_child_abort, once=True)

    return child
