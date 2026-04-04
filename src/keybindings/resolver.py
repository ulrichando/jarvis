"""Keybinding resolver -- resolve actions from keystroke events."""

from __future__ import annotations

from typing import Optional

from .defaultBindings import DEFAULT_BINDINGS
from .match import matches_keystroke
from .parser import parse_keystroke
from .types import KeybindingBlock, ParsedKeystroke, ResolvedBinding


class KeybindingResolver:
    """Resolves keystroke events to bound actions."""

    def __init__(self, user_bindings: list[KeybindingBlock] | None = None) -> None:
        self._bindings = list(DEFAULT_BINDINGS)
        if user_bindings:
            self._bindings.extend(user_bindings)

    def resolve(self, context: str, event: ParsedKeystroke) -> Optional[str]:
        """Resolve a keystroke event to an action name."""
        for block in reversed(self._bindings):
            if block.context != context and block.context != "Global":
                continue
            for key_str, action in block.bindings.items():
                binding = parse_keystroke(key_str)
                if matches_keystroke(event, binding):
                    return action
        return None
