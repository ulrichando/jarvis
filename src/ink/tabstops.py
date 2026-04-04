"""Tab expansion, inspired by Ghostty's Tabstops.zig.

Uses 8-column intervals (POSIX default).
"""

from __future__ import annotations

import re

from .string_width import string_width
from .termio.tokenize import create_tokenizer

DEFAULT_TAB_INTERVAL = 8


def expand_tabs(text: str, interval: int = DEFAULT_TAB_INTERVAL) -> str:
    """Expand tab characters to spaces based on column position."""
    if "\t" not in text:
        return text

    tokenizer = create_tokenizer()
    tokens = tokenizer.feed(text)
    tokens.extend(tokenizer.flush())

    result = ""
    column = 0

    for token in tokens:
        if token.type == "sequence":
            result += token.value
        else:
            parts = re.split(r"(\t|\n)", token.value)
            for part in parts:
                if part == "\t":
                    spaces = interval - (column % interval)
                    result += " " * spaces
                    column += spaces
                elif part == "\n":
                    result += part
                    column = 0
                else:
                    result += part
                    column += string_width(part)

    return result
