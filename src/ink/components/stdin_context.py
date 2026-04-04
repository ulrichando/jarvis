"""Stdin context for Ink."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class StdinContext:
    """Context providing stdin access."""
    stdin: Any = None
    set_raw_mode: Callable[[bool], None] | None = None
    internal_raw_mode: bool = False
    is_raw_mode_supported: bool = False
