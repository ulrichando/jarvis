"""Terminal abstraction layer."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class TerminalSize:
    columns: int = 80
    rows: int = 24


class Terminal:
    """Terminal abstraction for input/output."""

    def __init__(self, stdin: Any = None, stdout: Any = None) -> None:
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self._raw_mode = False

    def get_size(self) -> TerminalSize:
        """Get terminal dimensions."""
        try:
            size = os.get_terminal_size()
            return TerminalSize(columns=size.columns, rows=size.lines)
        except OSError:
            return TerminalSize()

    def write(self, data: str) -> None:
        """Write data to stdout."""
        self.stdout.write(data)
        self.stdout.flush()

    def enable_raw_mode(self) -> None:
        """Enable raw terminal input mode."""
        try:
            import tty
            import termios
            self._old_settings = termios.tcgetattr(self.stdin)
            tty.setraw(self.stdin)
            self._raw_mode = True
        except (ImportError, termios.error):
            pass

    def disable_raw_mode(self) -> None:
        """Disable raw terminal input mode."""
        if self._raw_mode:
            try:
                import termios
                termios.tcsetattr(self.stdin, termios.TCSADRAIN, self._old_settings)
                self._raw_mode = False
            except (ImportError, Exception):
                pass

    @property
    def is_tty(self) -> bool:
        """Check if stdout is a TTY."""
        return hasattr(self.stdout, "isatty") and self.stdout.isatty()
