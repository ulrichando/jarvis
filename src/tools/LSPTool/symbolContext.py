"""Symbol context utilities for the LSP tool."""
from __future__ import annotations

from typing import Any, Optional


def get_symbol_at_position(
    file_content: str,
    line: int,
    character: int,
) -> Optional[str]:
    """Extract the symbol at a given position in file content."""
    lines = file_content.split("\n")
    if line < 0 or line >= len(lines):
        return None
    line_text = lines[line]
    if character < 0 or character >= len(line_text):
        return None

    # Find word boundaries
    start = character
    while start > 0 and (line_text[start - 1].isalnum() or line_text[start - 1] == "_"):
        start -= 1

    end = character
    while end < len(line_text) and (line_text[end].isalnum() or line_text[end] == "_"):
        end += 1

    symbol = line_text[start:end]
    return symbol if symbol else None
