"""
FPS (frames per second) tracker for rendering performance monitoring.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FpsMetrics:
    """FPS measurement results."""

    average_fps: float
    low_1pct_fps: float


class FpsTracker:
    """Tracks frame durations and computes FPS metrics."""

    def __init__(self) -> None:
        self._frame_durations: list[float] = []
        self._first_render_time: Optional[float] = None
        self._last_render_time: Optional[float] = None

    def record(self, duration_ms: float) -> None:
        """Record a frame's render duration in milliseconds."""
        now = time.perf_counter() * 1000  # Convert to ms
        if self._first_render_time is None:
            self._first_render_time = now
        self._last_render_time = now
        self._frame_durations.append(duration_ms)

    def get_metrics(self) -> Optional[FpsMetrics]:
        """
        Calculate FPS metrics from recorded frames.

        Returns None if insufficient data is available.
        """
        if (
            not self._frame_durations
            or self._first_render_time is None
            or self._last_render_time is None
        ):
            return None

        total_time_ms = self._last_render_time - self._first_render_time
        if total_time_ms <= 0:
            return None

        total_frames = len(self._frame_durations)
        average_fps = total_frames / (total_time_ms / 1000)

        # Calculate 1st percentile (worst 1%) FPS
        sorted_durations = sorted(self._frame_durations, reverse=True)
        p99_index = max(0, int(len(sorted_durations) * 0.01))
        p99_frame_time_ms = sorted_durations[p99_index]
        low_1pct_fps = 1000 / p99_frame_time_ms if p99_frame_time_ms > 0 else 0

        return FpsMetrics(
            average_fps=round(average_fps * 100) / 100,
            low_1pct_fps=round(low_1pct_fps * 100) / 100,
        )
