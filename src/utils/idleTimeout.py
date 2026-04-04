"""
Idle timeout manager for SDK mode.
Automatically exits the process after the specified idle duration.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Callable, Optional


class IdleTimeoutManager:
    """Manages idle timeout for automatic exit."""

    def __init__(self, is_idle: Callable[[], bool]) -> None:
        self._is_idle = is_idle
        delay_str = os.environ.get("CLAUDE_CODE_EXIT_AFTER_STOP_DELAY")
        self._delay_ms: Optional[int] = None
        if delay_str:
            try:
                val = int(delay_str)
                if val > 0:
                    self._delay_ms = val
            except ValueError:
                pass
        self._timer: Optional[threading.Timer] = None
        self._last_idle_time: float = 0

    def start(self) -> None:
        """Start the idle timeout timer."""
        self.stop()
        if self._delay_ms is not None:
            self._last_idle_time = time.time() * 1000

            def check_idle() -> None:
                idle_duration = time.time() * 1000 - self._last_idle_time
                if self._is_idle() and idle_duration >= self._delay_ms:  # type: ignore
                    import sys
                    sys.exit(0)

            self._timer = threading.Timer(self._delay_ms / 1000, check_idle)
            self._timer.daemon = True
            self._timer.start()

    def stop(self) -> None:
        """Stop the idle timeout timer."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None


def create_idle_timeout_manager(
    is_idle: Callable[[], bool],
) -> IdleTimeoutManager:
    """Create an idle timeout manager."""
    return IdleTimeoutManager(is_idle)
