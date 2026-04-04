"""Throttle value changes so each value stays visible for a minimum time."""

from __future__ import annotations

import time
from typing import Generic, Optional, TypeVar

T = TypeVar("T")


class MinDisplayTime(Generic[T]):
    """Throttles a value so each distinct value stays visible for at least min_ms.

    Prevents fast-cycling progress text from flickering past before it is readable.
    Unlike debounce (wait for quiet) or throttle (limit rate), this guarantees
    each value gets its minimum screen time before being replaced.

    Equivalent to useMinDisplayTime React hook.
    """

    def __init__(self, initial_value: T, min_ms: int):
        self._displayed = initial_value
        self._min_ms = min_ms
        self._last_shown_at: float = 0
        self._pending_value: Optional[T] = None
        self._pending_timer: Optional[float] = None

    @property
    def displayed(self) -> T:
        """Get the currently displayed value."""
        # Check if pending value should now be shown
        if self._pending_timer is not None:
            now = time.time() * 1000
            if now >= self._pending_timer:
                self._displayed = self._pending_value
                self._last_shown_at = now
                self._pending_value = None
                self._pending_timer = None
        return self._displayed

    def update(self, value: T) -> T:
        """Update the value, respecting minimum display time.

        Returns the value that should be displayed right now.
        """
        now = time.time() * 1000
        elapsed = now - self._last_shown_at

        if elapsed >= self._min_ms:
            self._last_shown_at = now
            self._displayed = value
            self._pending_value = None
            self._pending_timer = None
        else:
            # Schedule the value for later
            self._pending_value = value
            self._pending_timer = self._last_shown_at + self._min_ms

        return self._displayed
