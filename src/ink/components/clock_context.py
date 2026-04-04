"""Clock context for periodic updates."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ClockContext:
    """Context providing a clock tick for components that need periodic updates."""
    tick: int = 0
    interval_ms: int = 1000
