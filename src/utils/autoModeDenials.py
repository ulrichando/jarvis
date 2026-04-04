"""
Tracks commands recently denied by the auto mode classifier.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

MAX_DENIALS = 20


@dataclass
class AutoModeDenial:
    tool_name: str
    display: str  # Human-readable description of the denied command
    reason: str
    timestamp: float


_denials: list[AutoModeDenial] = []


def record_auto_mode_denial(denial: AutoModeDenial) -> None:
    """Record a new auto mode denial."""
    global _denials
    _denials = [denial] + _denials[: MAX_DENIALS - 1]


def get_auto_mode_denials() -> Sequence[AutoModeDenial]:
    """Get all recorded auto mode denials."""
    return _denials
