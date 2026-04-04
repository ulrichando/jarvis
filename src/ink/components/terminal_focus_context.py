"""Terminal focus context."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TerminalFocusContext:
    """Context providing terminal window focus state."""
    is_focused: bool = True
