"""useTerminalFocus hook - track terminal window focus."""
from __future__ import annotations


class UseTerminalFocus:
    """Tracks whether the terminal window is focused."""

    def __init__(self) -> None:
        self.is_focused: bool = True

    def set_focused(self, focused: bool) -> None:
        self.is_focused = focused
