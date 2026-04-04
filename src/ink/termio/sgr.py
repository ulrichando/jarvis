"""SGR (Select Graphic Rendition) Parser.

Parses SGR parameters and applies them to a TextStyle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .types import TextStyle, default_style

NAMED_COLORS = [
    "black", "red", "green", "yellow", "blue", "magenta", "cyan", "white",
    "brightBlack", "brightRed", "brightGreen", "brightYellow",
    "brightBlue", "brightMagenta", "brightCyan", "brightWhite",
]

UNDERLINE_STYLES = ["none", "single", "double", "curly", "dotted", "dashed"]


@dataclass
class Param:
    value: int | None = None
    subparams: list[int] = field(default_factory=list)
    colon: bool = False


def _parse_params(s: str) -> list[Param]:
    if s == "":
        return [Param(value=0)]

    result: list[Param] = []
    current = Param()
    num = ""
    in_sub = False

    for i in range(len(s) + 1):
        c = s[i] if i < len(s) else None
        if c == ";" or c is None:
            n = None if num == "" else int(num)
            if in_sub:
                if n is not None:
                    current.subparams.append(n)
            else:
                current.value = n
            result.append(current)
            current = Param()
            num = ""
            in_sub = False
        elif c == ":":
            n = None if num == "" else int(num)
            if not in_sub:
                current.value = n
                current.colon = True
                in_sub = True
            else:
                if n is not None:
                    current.subparams.append(n)
            num = ""
        elif c is not None and "0" <= c <= "9":
            num += c

    return result


def _parse_extended_color(
    params: list[Param], idx: int
) -> dict[str, Any] | None:
    if idx >= len(params):
        return None
    p = params[idx]

    if p.colon and len(p.subparams) >= 1:
        if p.subparams[0] == 5 and len(p.subparams) >= 2:
            return {"index": p.subparams[1]}
        if p.subparams[0] == 2 and len(p.subparams) >= 4:
            off = 1 if len(p.subparams) >= 5 else 0
            return {
                "r": p.subparams[1 + off],
                "g": p.subparams[2 + off],
                "b": p.subparams[3 + off],
            }

    if idx + 1 >= len(params):
        return None
    next_p = params[idx + 1]
    if (
        next_p.value == 5
        and idx + 2 < len(params)
        and params[idx + 2].value is not None
    ):
        return {"index": params[idx + 2].value}
    if next_p.value == 2:
        r = params[idx + 2].value if idx + 2 < len(params) else None
        g = params[idx + 3].value if idx + 3 < len(params) else None
        b = params[idx + 4].value if idx + 4 < len(params) else None
        if r is not None and g is not None and b is not None:
            return {"r": r, "g": g, "b": b}
    return None


def apply_sgr(param_str: str, style: TextStyle) -> TextStyle:
    """Apply SGR parameters to a style, returning a new style."""
    params = _parse_params(param_str)
    s = style.copy()
    i = 0

    while i < len(params):
        p = params[i]
        code = p.value if p.value is not None else 0

        if code == 0:
            s = default_style()
        elif code == 1:
            s.bold = True
        elif code == 2:
            s.dim = True
        elif code == 3:
            s.italic = True
        elif code == 4:
            if p.colon and p.subparams:
                idx = p.subparams[0]
                s.underline = UNDERLINE_STYLES[idx] if idx < len(UNDERLINE_STYLES) else "single"
            else:
                s.underline = "single"
        elif code in (5, 6):
            s.blink = True
        elif code == 7:
            s.inverse = True
        elif code == 8:
            s.hidden = True
        elif code == 9:
            s.strikethrough = True
        elif code == 21:
            s.underline = "double"
        elif code == 22:
            s.bold = False
            s.dim = False
        elif code == 23:
            s.italic = False
        elif code == 24:
            s.underline = "none"
        elif code == 25:
            s.blink = False
        elif code == 27:
            s.inverse = False
        elif code == 28:
            s.hidden = False
        elif code == 29:
            s.strikethrough = False
        elif code == 53:
            s.overline = True
        elif code == 55:
            s.overline = False
        elif 30 <= code <= 37:
            s.fg = {"type": "named", "name": NAMED_COLORS[code - 30]}
        elif code == 39:
            s.fg = {"type": "default"}
        elif 40 <= code <= 47:
            s.bg = {"type": "named", "name": NAMED_COLORS[code - 40]}
        elif code == 49:
            s.bg = {"type": "default"}
        elif 90 <= code <= 97:
            s.fg = {"type": "named", "name": NAMED_COLORS[code - 90 + 8]}
        elif 100 <= code <= 107:
            s.bg = {"type": "named", "name": NAMED_COLORS[code - 100 + 8]}
        elif code == 38:
            c = _parse_extended_color(params, i)
            if c:
                if "index" in c:
                    s.fg = {"type": "indexed", "index": c["index"]}
                else:
                    s.fg = {"type": "rgb", "r": c["r"], "g": c["g"], "b": c["b"]}
                i += 1 if p.colon else (3 if "index" in c else 5)
                continue
        elif code == 48:
            c = _parse_extended_color(params, i)
            if c:
                if "index" in c:
                    s.bg = {"type": "indexed", "index": c["index"]}
                else:
                    s.bg = {"type": "rgb", "r": c["r"], "g": c["g"], "b": c["b"]}
                i += 1 if p.colon else (3 if "index" in c else 5)
                continue
        elif code == 58:
            c = _parse_extended_color(params, i)
            if c:
                if "index" in c:
                    s.underline_color = {"type": "indexed", "index": c["index"]}
                else:
                    s.underline_color = {"type": "rgb", "r": c["r"], "g": c["g"], "b": c["b"]}
                i += 1 if p.colon else (3 if "index" in c else 5)
                continue
        elif code == 59:
            s.underline_color = {"type": "default"}

        i += 1

    return s
