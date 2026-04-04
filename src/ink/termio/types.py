"""ANSI Parser - Semantic Types.

These types represent the semantic meaning of ANSI escape sequences,
not their string representation. Inspired by ghostty's action-based design.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Union

# =============================================================================
# Colors
# =============================================================================

NamedColor = Literal[
    "black", "red", "green", "yellow", "blue", "magenta", "cyan", "white",
    "brightBlack", "brightRed", "brightGreen", "brightYellow",
    "brightBlue", "brightMagenta", "brightCyan", "brightWhite",
]

# Color is represented as a dict with a 'type' key
# {'type': 'named', 'name': NamedColor}
# {'type': 'indexed', 'index': int}
# {'type': 'rgb', 'r': int, 'g': int, 'b': int}
# {'type': 'default'}
Color = dict[str, Any]

# =============================================================================
# Text Styles
# =============================================================================

UnderlineStyle = Literal["none", "single", "double", "curly", "dotted", "dashed"]


@dataclass
class TextStyle:
    """Text style attributes - represents current styling state."""
    bold: bool = False
    dim: bool = False
    italic: bool = False
    underline: str = "none"  # UnderlineStyle
    blink: bool = False
    inverse: bool = False
    hidden: bool = False
    strikethrough: bool = False
    overline: bool = False
    fg: dict[str, Any] = field(default_factory=lambda: {"type": "default"})
    bg: dict[str, Any] = field(default_factory=lambda: {"type": "default"})
    underline_color: dict[str, Any] = field(default_factory=lambda: {"type": "default"})

    def copy(self) -> TextStyle:
        return TextStyle(
            bold=self.bold, dim=self.dim, italic=self.italic,
            underline=self.underline, blink=self.blink, inverse=self.inverse,
            hidden=self.hidden, strikethrough=self.strikethrough,
            overline=self.overline,
            fg=dict(self.fg), bg=dict(self.bg),
            underline_color=dict(self.underline_color),
        )


def default_style() -> TextStyle:
    """Create a default (reset) text style."""
    return TextStyle()


def styles_equal(a: TextStyle, b: TextStyle) -> bool:
    """Check if two styles are equal."""
    return (
        a.bold == b.bold
        and a.dim == b.dim
        and a.italic == b.italic
        and a.underline == b.underline
        and a.blink == b.blink
        and a.inverse == b.inverse
        and a.hidden == b.hidden
        and a.strikethrough == b.strikethrough
        and a.overline == b.overline
        and colors_equal(a.fg, b.fg)
        and colors_equal(a.bg, b.bg)
        and colors_equal(a.underline_color, b.underline_color)
    )


def colors_equal(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Check if two colors are equal."""
    if a.get("type") != b.get("type"):
        return False
    t = a.get("type")
    if t == "named":
        return a.get("name") == b.get("name")
    if t == "indexed":
        return a.get("index") == b.get("index")
    if t == "rgb":
        return a.get("r") == b.get("r") and a.get("g") == b.get("g") and a.get("b") == b.get("b")
    if t == "default":
        return True
    return False


# =============================================================================
# Cursor Actions
# =============================================================================

CursorDirection = Literal["up", "down", "forward", "back"]

# CursorAction, EraseAction, etc. are represented as dicts with a 'type' key

# =============================================================================
# Parsed Segments
# =============================================================================

@dataclass
class TextSegment:
    """A segment of styled text."""
    type: str = "text"
    text: str = ""
    style: TextStyle = field(default_factory=TextStyle)


@dataclass
class Grapheme:
    """A grapheme (visual character unit) with width info."""
    value: str = ""
    width: int = 1  # 1 or 2

# Action is represented as a dict with a 'type' key
Action = dict[str, Any]

# Type aliases for documentation
CursorAction = dict[str, Any]
EraseAction = dict[str, Any]
ScrollAction = dict[str, Any]
ModeAction = dict[str, Any]
LinkAction = dict[str, Any]
TitleAction = dict[str, Any]
TabStatusAction = dict[str, Any]
