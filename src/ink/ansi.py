"""Ansi component - renders pre-built ANSI-styled text.

In the TS version this is a React component that parses ANSI escape
sequences and renders styled text nodes. Here it provides the same
parsing and style extraction logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .termio.parser import Parser
from .termio.types import Grapheme, TextStyle


@dataclass
class AnsiSegment:
    """A segment of text with its style from ANSI parsing."""
    text: str = ""
    style: TextStyle = field(default_factory=TextStyle)
    hyperlink: str | None = None


def parse_ansi(text: str) -> list[AnsiSegment]:
    """Parse ANSI-escaped text into styled segments.

    This is the logic from the Ansi.tsx component, extracted for
    non-React usage.
    """
    parser = Parser()
    actions = parser.feed(text)
    segments: list[AnsiSegment] = []

    for action in actions:
        if action["type"] == "text":
            graphemes = action.get("graphemes", [])
            text_content = "".join(
                g.value if isinstance(g, Grapheme) else g.get("value", "")
                for g in graphemes
            )
            style = action.get("style", TextStyle())
            segments.append(AnsiSegment(
                text=text_content,
                style=style if isinstance(style, TextStyle) else TextStyle(),
                hyperlink=parser.link_url,
            ))

    return segments
