"""Terminal focus state tracking."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TerminalFocusState:
    """Tracks whether the terminal window has focus."""
    has_focus: bool = True

    def set_focused(self, focused: bool) -> None:
        self.has_focus = focused
