"""useTerminalTitle hook - set the terminal title."""
from __future__ import annotations
import sys

from ..termio.osc import osc, OSC


class UseTerminalTitle:
    """Sets the terminal window title via OSC 2."""

    def __init__(self) -> None:
        self._title: str = ""

    def set_title(self, title: str) -> None:
        self._title = title
        seq = osc(OSC.SET_TITLE, title)
        sys.stdout.write(seq)
        sys.stdout.flush()

    def clear(self) -> None:
        self._title = ""
