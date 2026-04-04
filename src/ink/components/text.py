"""Text component - renders styled text."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class TextProps:
    """Properties for the Text component."""
    color: str | None = None
    background_color: str | None = None
    dim: bool = False
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strikethrough: bool = False
    inverse: bool = False
    wrap: str | None = None  # 'wrap' | 'truncate' | 'truncate-start' | 'truncate-middle' | 'truncate-end'


class Text:
    """Renders styled text. In TS this renders an ink-text DOM element."""

    def __init__(self, props: TextProps | None = None, content: str = "") -> None:
        self.props = props or TextProps()
        self.content = content
