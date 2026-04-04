"""React hook equivalent for shortcut display (Python logic only)."""

from __future__ import annotations

from .shortcutFormat import format_shortcut
from .types import ParsedKeystroke


def get_shortcut_display(key: str) -> str:
    """Get display string for a shortcut key."""
    from .parser import parse_keystroke
    keystroke = parse_keystroke(key)
    return format_shortcut(keystroke)
