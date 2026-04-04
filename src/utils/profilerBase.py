"""Shared infrastructure for profiler modules."""

from __future__ import annotations

import time
from typing import Optional


def format_ms(ms: float) -> str:
    """Format milliseconds with 3 decimal places."""
    return f"{ms:.3f}"


def _format_file_size(size: int) -> str:
    """Format a file size in human-readable form."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def format_timeline_line(
    total_ms: float,
    delta_ms: float,
    name: str,
    memory: Optional[dict[str, int]] = None,
    total_pad: int = 8,
    delta_pad: int = 7,
    extra: str = "",
) -> str:
    """Render a single timeline line in the shared profiler report format.

    Format: [+ total.ms] (+ delta.ms) name [extra] [| RSS: .., Heap: ..]

    Args:
        total_ms: Total elapsed time in milliseconds
        delta_ms: Delta from previous event in milliseconds
        name: Event name
        memory: Optional dict with 'rss' and 'heap_used' keys (bytes)
        total_pad: padStart width for total column
        delta_pad: padStart width for delta column
        extra: Additional text to append
    """
    mem_info = ""
    if memory:
        rss = _format_file_size(memory.get("rss", 0))
        heap = _format_file_size(memory.get("heap_used", 0))
        mem_info = f" | RSS: {rss}, Heap: {heap}"

    total_str = format_ms(total_ms).rjust(total_pad)
    delta_str = format_ms(delta_ms).rjust(delta_pad)
    return f"[+{total_str}ms] (+{delta_str}ms) {name}{extra}{mem_info}"


class Profiler:
    """Simple profiler that tracks timing events."""

    def __init__(self) -> None:
        self._start_time: float = time.monotonic()
        self._last_time: float = self._start_time
        self._events: list[tuple[float, float, str]] = []

    def mark(self, name: str) -> None:
        """Record a timing event."""
        now = time.monotonic()
        total_ms = (now - self._start_time) * 1000
        delta_ms = (now - self._last_time) * 1000
        self._events.append((total_ms, delta_ms, name))
        self._last_time = now

    def report(self, total_pad: int = 8, delta_pad: int = 7) -> str:
        """Generate a report of all timing events."""
        lines = [
            format_timeline_line(total, delta, name, total_pad=total_pad, delta_pad=delta_pad)
            for total, delta, name in self._events
        ]
        return "\n".join(lines)
