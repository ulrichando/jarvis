"""Format keybindings for display."""

from __future__ import annotations

import sys
from .types import ParsedKeystroke


def format_shortcut(keystroke: ParsedKeystroke, platform: str | None = None) -> str:
    """Format a keystroke for display."""
    plat = platform or sys.platform
    is_mac = plat == "darwin"
    parts = []
    if keystroke.ctrl:
        parts.append("\u2303" if is_mac else "Ctrl")
    if keystroke.alt:
        parts.append("\u2325" if is_mac else "Alt")
    if keystroke.shift:
        parts.append("\u21e7" if is_mac else "Shift")
    if keystroke.meta:
        parts.append("Meta")
    if keystroke.super_key:
        parts.append("\u2318" if is_mac else "Super")
    key = keystroke.key.capitalize() if len(keystroke.key) > 1 else keystroke.key
    parts.append(key)
    return "+".join(parts) if not is_mac else "".join(parts)
