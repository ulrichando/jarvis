"""Synchronized blinking animation state."""

from __future__ import annotations

import time

BLINK_INTERVAL_MS = 600


class BlinkState:
    """Manages blink animation state.

    All instances using the same clock blink together.
    Equivalent to useBlink React hook.
    """

    def __init__(self, enabled: bool = True, interval_ms: int = BLINK_INTERVAL_MS):
        self.enabled = enabled
        self.interval_ms = interval_ms
        self._start_time = time.monotonic()

    @property
    def is_visible(self) -> bool:
        """Return True when visible in blink cycle."""
        if not self.enabled:
            return True
        elapsed_ms = (time.monotonic() - self._start_time) * 1000
        return int(elapsed_ms / self.interval_ms) % 2 == 0

    def reset(self) -> None:
        """Reset the blink timer."""
        self._start_time = time.monotonic()
