"""Event fired when the terminal window gains or loses focus.

Uses DECSET 1004 focus reporting:
  CSI I when the terminal gains focus
  CSI O when the terminal loses focus
"""

from typing import Literal

from .event import Event

TerminalFocusEventType = Literal["terminalfocus", "terminalblur"]


class TerminalFocusEvent(Event):
    """Terminal window focus/blur event."""

    def __init__(self, type_: TerminalFocusEventType) -> None:
        super().__init__()
        self.type: TerminalFocusEventType = type_
