"""Terminal size detection."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass


@dataclass
class TerminalSize:
    columns: int
    rows: int


def get_terminal_size() -> TerminalSize:
    """Get the current terminal size.

    Equivalent to useTerminalSize React hook.
    """
    size = shutil.get_terminal_size(fallback=(80, 24))
    return TerminalSize(columns=size.columns, rows=size.lines)
