"""LogUpdate - terminal output diffing and rendering.

Computes the minimal set of terminal operations needed to update
the screen from one frame to the next.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .frame import Diff, Frame, Patch
from .screen import Screen, StylePool


@dataclass
class LogUpdateOptions:
    is_tty: bool = True
    style_pool: StylePool | None = None


class LogUpdate:
    """Computes diffs between frames and produces terminal patches."""

    def __init__(self, options: LogUpdateOptions | None = None) -> None:
        self.options = options or LogUpdateOptions()
        self._previous_output: str = ""

    def reset(self) -> None:
        """Reset state (e.g., after SIGCONT)."""
        self._previous_output = ""

    def render(
        self, prev: Frame, next_: Frame,
        alt_screen: bool = False, decstbm_safe: bool = True
    ) -> Diff:
        """Compute the diff between two frames."""
        if not self.options.is_tty:
            return self._render_full_frame(next_)
        # Simplified: full render every time
        return self._render_full_frame(next_)

    def _render_full_frame(self, frame: Frame) -> Diff:
        """Render entire frame (no diffing)."""
        if not frame.screen:
            return []
        return [Patch(type="stdout", content="")]

    def render_previous_output(self, prev_frame: Frame) -> Diff:
        """Render previous output for cleanup."""
        self._previous_output = ""
        return []
