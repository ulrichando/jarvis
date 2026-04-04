"""Keybinding parser -- parse keystroke strings into structured objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .types import ParsedKeystroke, ParsedBinding


def parse_keystroke(input_str: str) -> ParsedKeystroke:
    """Parse a keystroke string like 'ctrl+shift+k' into a ParsedKeystroke."""
    parts = input_str.split("+")
    keystroke = ParsedKeystroke(key="", ctrl=False, alt=False, shift=False, meta=False, super_key=False)

    for part in parts:
        lower = part.lower()
        if lower in ("ctrl", "control"):
            keystroke.ctrl = True
        elif lower in ("alt", "opt", "option"):
            keystroke.alt = True
        elif lower == "shift":
            keystroke.shift = True
        elif lower == "meta":
            keystroke.meta = True
        elif lower in ("cmd", "command", "super", "win"):
            keystroke.super_key = True
        elif lower == "esc":
            keystroke.key = "escape"
        elif lower == "return":
            keystroke.key = "enter"
        elif lower == "space":
            keystroke.key = " "
        elif lower == "tab":
            keystroke.key = "tab"
        elif lower == "backspace":
            keystroke.key = "backspace"
        elif lower == "delete":
            keystroke.key = "delete"
        elif lower in ("up", "down", "left", "right"):
            keystroke.key = lower
        elif lower in ("home", "end", "pageup", "pagedown"):
            keystroke.key = lower
        else:
            keystroke.key = lower

    return keystroke


def parse_binding(binding_str: str) -> ParsedBinding:
    """Parse a binding string, which may be a chord (e.g., 'ctrl+k ctrl+c')."""
    parts = binding_str.strip().split(" ")
    keystrokes = [parse_keystroke(p) for p in parts if p]
    return ParsedBinding(keystrokes=keystrokes)
