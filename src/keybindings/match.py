"""Keybinding matching -- match input events to bound actions."""

from __future__ import annotations

from typing import Optional

from .types import ParsedKeystroke, ResolvedBinding


def matches_keystroke(event: ParsedKeystroke, binding: ParsedKeystroke) -> bool:
    """Check if an input event matches a keybinding."""
    return (
        event.key == binding.key
        and event.ctrl == binding.ctrl
        and event.alt == binding.alt
        and event.shift == binding.shift
        and event.meta == binding.meta
        and event.super_key == binding.super_key
    )
