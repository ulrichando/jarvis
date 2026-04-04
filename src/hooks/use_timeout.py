"""Simple timeout state tracking."""

from __future__ import annotations

import time
from typing import Optional


class TimeoutTracker:
    """Tracks whether a timeout has elapsed.

    Equivalent to useTimeout React hook.
    """

    def __init__(self, delay_ms: int):
        self.delay_ms = delay_ms
        self._start_time = time.time() * 1000

    @property
    def is_elapsed(self) -> bool:
        return (time.time() * 1000 - self._start_time) >= self.delay_ms

    def reset(self) -> None:
        self._start_time = time.time() * 1000
