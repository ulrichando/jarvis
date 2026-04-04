"""Keybinding type definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ParsedKeystroke:
    key: str = ""
    ctrl: bool = False
    alt: bool = False
    shift: bool = False
    meta: bool = False
    super_key: bool = False


@dataclass
class ParsedBinding:
    keystrokes: list[ParsedKeystroke] = field(default_factory=list)


@dataclass
class Chord:
    keys: list[str] = field(default_factory=list)


@dataclass
class KeybindingBlock:
    context: str = ""
    bindings: dict[str, str] = field(default_factory=dict)


@dataclass
class ResolvedBinding:
    action: str = ""
    keystroke: ParsedKeystroke = field(default_factory=ParsedKeystroke)
    context: str = ""
    source: str = "default"  # 'default' | 'user'
