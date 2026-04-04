"""
Keyboard shortcut mapping for macOS Option+key combos.

Maps special characters produced by macOS Option+key to their
keybinding equivalents. Used when terminals don't have
"Option as Meta" enabled.
"""

from __future__ import annotations

from typing import Optional

# Special characters that macOS Option+key produces
MACOS_OPTION_SPECIAL_CHARS: dict[str, str] = {
    "\u2020": "alt+t",  # Option+T (dagger) -> thinking toggle
    "\u03c0": "alt+p",  # Option+P (pi) -> model picker
    "\u00f8": "alt+o",  # Option+O (o-slash) -> fast mode
}


def is_macos_option_char(char: str) -> bool:
    """Check if a character is a macOS Option+key special character."""
    return char in MACOS_OPTION_SPECIAL_CHARS


def get_macos_option_binding(char: str) -> Optional[str]:
    """Get the keybinding for a macOS Option+key special character."""
    return MACOS_OPTION_SPECIAL_CHARS.get(char)
