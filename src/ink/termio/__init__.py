"""ANSI Parser Module.

A semantic ANSI escape sequence parser inspired by ghostty, tmux, and iTerm2.
"""

from .parser import Parser
from .types import (
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
