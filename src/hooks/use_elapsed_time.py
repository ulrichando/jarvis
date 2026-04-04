"""Elapsed time tracking with formatted duration output."""

from __future__ import annotations

import time


def format_duration(ms: int) -> str:
    """Format milliseconds into human-readable duration string.

    Examples: '1s', '1m 23s', '1h 2m 3s'
    """
    if ms < 1000:
        return f"{ms}ms"

    seconds = ms // 1000
    if seconds < 60:
        return f"{seconds}s"

    minutes = seconds // 60
    secs = seconds % 60
    if minutes < 60:
        return f"{minutes}m {secs}s" if secs else f"{minutes}m"

    hours = minutes // 60
    mins = minutes % 60
    parts = [f"{hours}h"]
    if mins:
        parts.append(f"{mins}m")
    if secs:
        parts.append(f"{secs}s")
    return " ".join(parts)


class ElapsedTimeTracker:
    """Tracks elapsed time with pause support.

    Equivalent to useElapsedTime React hook.

    Args:
        start_time: Start timestamp in seconds (time.time()).
        is_running: Whether the timer is actively running.
        paused_ms: Total paused duration in milliseconds to subtract.
        end_time: If set, freezes duration at this timestamp.
    """

    def __init__(
        self,
        start_time: float,
        is_running: bool = True,
        paused_ms: int = 0,
        end_time: float | None = None,
    ):
        self.start_time = start_time
        self.is_running = is_running
        self.paused_ms = paused_ms
        self.end_time = end_time

    @property
    def elapsed_ms(self) -> int:
        """Get elapsed time in milliseconds."""
        end = self.end_time if self.end_time is not None else time.time()
        return max(0, int((end - self.start_time) * 1000) - self.paused_ms)

    @property
    def formatted(self) -> str:
        """Get formatted elapsed time string."""
        return format_duration(self.elapsed_ms)
