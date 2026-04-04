"""ANSI-aware text wrapping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .string_width import string_width


@dataclass
class WrapAnsiOptions:
    hard: bool = False
    word_wrap: bool = True
    trim: bool = False


def wrap_ansi(
    input_: str,
    columns: int,
    options: WrapAnsiOptions | None = None,
) -> str:
    """Wrap text at the specified column width, preserving ANSI codes."""
    if options is None:
        options = WrapAnsiOptions()

    lines = input_.split("\n")
    result: list[str] = []

    for line in lines:
        width = string_width(line)
        if width <= columns:
            result.append(line.rstrip() if options.trim else line)
        elif options.hard:
            current = ""
            current_width = 0
            for ch in line:
                ch_w = string_width(ch)
                if current_width + ch_w > columns and current:
                    result.append(current.rstrip() if options.trim else current)
                    current = ch
                    current_width = ch_w
                else:
                    current += ch
                    current_width += ch_w
            if current:
                result.append(current.rstrip() if options.trim else current)
        else:
            result.append(line.rstrip() if options.trim else line)

    return "\n".join(result)
