"""Terminal size context."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TerminalSizeContext:
    """Context providing terminal dimensions."""
    columns: int = 80
    rows: int = 24
