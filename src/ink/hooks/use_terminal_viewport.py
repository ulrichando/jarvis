"""useTerminalViewport hook - track terminal viewport size."""
from __future__ import annotations
import os


class UseTerminalViewport:
    """Tracks terminal viewport dimensions."""

    def __init__(self) -> None:
        self._update()

    def _update(self) -> None:
        try:
            size = os.get_terminal_size()
            self.columns = size.columns
            self.rows = size.lines
        except OSError:
            self.columns = 80
            self.rows = 24

    def refresh(self) -> None:
        self._update()
