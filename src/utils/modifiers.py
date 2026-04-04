"""Modifier key detection (macOS only via native module)."""

from __future__ import annotations

import sys
from typing import Literal

ModifierKey = Literal["shift", "command", "control", "option"]

_prewarmed = False


def prewarm_modifiers() -> None:
    """Pre-warm the native module by loading it in advance (macOS only)."""
    global _prewarmed
    if _prewarmed or sys.platform != "darwin":
        return
    _prewarmed = True
    # On macOS, this would load a native module
    # No-op on other platforms


def is_modifier_pressed(modifier: ModifierKey) -> bool:
    """Check if a specific modifier key is currently pressed (macOS only)."""
    if sys.platform != "darwin":
        return False
    # Would require native module binding on macOS
    return False
