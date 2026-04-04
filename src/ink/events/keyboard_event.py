"""Keyboard event dispatched through the DOM tree via capture/bubble.

Follows browser KeyboardEvent semantics: 'key' is the literal character
for printable keys and a multi-char name for special keys.
"""

from __future__ import annotations

from .input_event import ParsedKey
from .terminal_event import TerminalEvent, TerminalEventInit


class KeyboardEvent(TerminalEvent):
    """Keyboard event with key information."""

    def __init__(self, parsed_key: ParsedKey) -> None:
        super().__init__("keydown", TerminalEventInit(bubbles=True, cancelable=True))
        self.key: str = _key_from_parsed(parsed_key)
        self.ctrl: bool = parsed_key.ctrl
        self.shift: bool = parsed_key.shift
        self.meta: bool = parsed_key.meta or parsed_key.option
        self.super_key: bool = parsed_key.super_
        self.fn: bool = parsed_key.fn


def _key_from_parsed(parsed: ParsedKey) -> str:
    """Extract key string from parsed keypress."""
    seq = parsed.sequence or ""
    name = parsed.name or ""

    # Ctrl combos: use the letter name
    if parsed.ctrl:
        return name

    # Single printable char
    if len(seq) == 1:
        code = ord(seq[0])
        if code >= 0x20 and code != 0x7F:
            return seq

    # Special keys: use the parsed name
    return name or seq
