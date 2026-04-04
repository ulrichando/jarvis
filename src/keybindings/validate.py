"""Keybinding validation."""

from __future__ import annotations

from typing import Optional

from .reservedShortcuts import RESERVED_SHORTCUTS
from .schema import KEYBINDING_CONTEXTS


def validate_keybinding(context: str, key: str, action: str) -> Optional[str]:
    """Validate a keybinding. Returns error message or None."""
    if context not in KEYBINDING_CONTEXTS:
        return f"Unknown context: {context}"
    if key in RESERVED_SHORTCUTS:
        return f"Cannot override reserved shortcut: {key}"
    if not action:
        return "Action cannot be empty"
    return None
