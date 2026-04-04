"""ANSI Parser Module - convenience re-exports.

See termio/ package for the full implementation.
"""

from .termio import (
    Parser,
    Action,
    Color,
    CursorAction,
    CursorDirection,
    EraseAction,
    Grapheme,
    LinkAction,
    ModeAction,
    NamedColor,
    ScrollAction,
    TextSegment,
    TextStyle,
    TitleAction,
    UnderlineStyle,
    colors_equal,
    default_style,
    styles_equal,
)
