"""Border rendering for Box elements."""

from __future__ import annotations

from typing import Any, Literal

BorderStyle = Literal["single", "double", "round", "bold", "singleDouble", "doubleSingle", "classic"]

BORDER_CHARS: dict[str, dict[str, str]] = {
    "single": {
        "top_left": "\u250c", "top": "\u2500", "top_right": "\u2510",
        "left": "\u2502", "right": "\u2502",
        "bottom_left": "\u2514", "bottom": "\u2500", "bottom_right": "\u2518",
    },
    "double": {
        "top_left": "\u2554", "top": "\u2550", "top_right": "\u2557",
        "left": "\u2551", "right": "\u2551",
        "bottom_left": "\u255a", "bottom": "\u2550", "bottom_right": "\u255d",
    },
    "round": {
        "top_left": "\u256d", "top": "\u2500", "top_right": "\u256e",
        "left": "\u2502", "right": "\u2502",
        "bottom_left": "\u2570", "bottom": "\u2500", "bottom_right": "\u256f",
    },
    "bold": {
        "top_left": "\u250f", "top": "\u2501", "top_right": "\u2513",
        "left": "\u2503", "right": "\u2503",
        "bottom_left": "\u2517", "bottom": "\u2501", "bottom_right": "\u251b",
    },
    "classic": {
        "top_left": "+", "top": "-", "top_right": "+",
        "left": "|", "right": "|",
        "bottom_left": "+", "bottom": "-", "bottom_right": "+",
    },
}


class BorderTextOptions:
    """Options for text within borders."""

    def __init__(
        self,
        top: str = "",
        bottom: str = "",
        top_align: str = "left",
        bottom_align: str = "left",
    ) -> None:
        self.top = top
        self.bottom = bottom
        self.top_align = top_align
        self.bottom_align = bottom_align


def render_border(
    width: int,
    height: int,
    style: str = "single",
    color: str | None = None,
    text: BorderTextOptions | None = None,
) -> list[str]:
    """Render a border as a list of lines."""
    chars = BORDER_CHARS.get(style, BORDER_CHARS["single"])
    lines: list[str] = []

    inner_width = max(0, width - 2)

    # Top border
    top_line = chars["top_left"] + chars["top"] * inner_width + chars["top_right"]
    lines.append(top_line)

    # Middle rows
    for _ in range(max(0, height - 2)):
        lines.append(chars["left"] + " " * inner_width + chars["right"])

    # Bottom border
    if height > 1:
        bottom_line = chars["bottom_left"] + chars["bottom"] * inner_width + chars["bottom_right"]
        lines.append(bottom_line)

    return lines
