"""Render screen buffer to terminal output."""

from __future__ import annotations

from typing import Any

from .frame import Diff
from .screen import Screen


def render_to_screen(
    screen: Screen,
    prev_screen: Screen | None = None,
) -> Diff:
    """Render the screen buffer to a list of terminal patches.

    Diffs against prev_screen to produce minimal output.
    """
    # Simplified - delegates to LogUpdate in the full implementation
    return []
