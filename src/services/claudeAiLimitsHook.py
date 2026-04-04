"""
Observer for JARVIS API limits (Python equivalent of React hook).

Provides a way to subscribe to rate limit status changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .claudeAiLimits import JarvisAPILimits, current_limits, status_listeners


class JarvisApiLimitsObserver:
    """Observer for JARVIS API limits changes.

    In the TypeScript version this was a React hook. In Python,
    this is a simple observer pattern.
    """

    def __init__(self) -> None:
        self._limits = JarvisAPILimits(**vars(current_limits))
        self._callback: Optional[Callable[[JarvisAPILimits], None]] = None

    def subscribe(self, callback: Callable[[JarvisAPILimits], None]) -> Callable[[], None]:
        """Subscribe to limits changes. Returns an unsubscribe function."""
        def listener(new_limits: JarvisAPILimits) -> None:
            self._limits = new_limits
            if self._callback:
                self._callback(new_limits)

        self._callback = callback
        status_listeners.add(listener)

        def unsubscribe():
            status_listeners.discard(listener)
            self._callback = None

        return unsubscribe

    @property
    def limits(self) -> JarvisAPILimits:
        return self._limits
