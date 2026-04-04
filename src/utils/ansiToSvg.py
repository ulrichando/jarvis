"""
Converts ANSI-escaped terminal text to SVG format.
Supports basic ANSI color codes (foreground colors).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from xml.sax.saxutils import escape as escape_xml


@dataclass
class AnsiColor:
    r: int
    g: int
    b: int


# Default terminal color palette
ANSI_COLORS: dict[int, AnsiColor] = {
    30: AnsiColor(0, 0, 0),        # black
    31: AnsiColor(205, 49, 49),     # red
    32: AnsiColor(13, 188, 121),    # green
    33: AnsiColor(229, 229, 16),    # yellow
    34: AnsiColor(36, 114, 200),    # blue
    35: AnsiColor(188, 63, 188),    # magenta
    36: AnsiColor(17, 168, 205),    # cyan
    37: AnsiColor(229, 229, 229),   # white
    # Bright colors
    90: AnsiColor(102, 102, 102),   # bright black (gray)
    91: AnsiColor(241, 76, 76),     # bright red
    92: AnsiColor(35, 209, 139),    # bright green
    93: AnsiColor(245, 245, 67),    # bright yellow
    94: AnsiColor(59, 142, 234),    # bright blue
    95: AnsiColor(214, 112, 214),   # bright magenta
    96: AnsiColor(41, 184, 219),    # bright cyan
    97: AnsiColor(255, 255, 255),   # bright white
}

DEFAULT_FG = AnsiColor(229, 229, 229)  # light gray
DEFAULT_BG = AnsiColor(30, 30, 30)     # dark gray


@dataclass
class TextSpan:
    text: str
    color: AnsiColor
    bold: bool


ParsedLine = list[TextSpan]


def _get_256_color(index: int) -> AnsiColor:
    """Get color from 256-color palette."""
    if index < 16:
        standard_colors = [
            AnsiColor(0, 0, 0), AnsiColor(128, 0, 0),
            AnsiColor(0, 128, 0), AnsiColor(128, 128, 0),
            AnsiColor(0, 0, 128), AnsiColor(128, 0, 128),
            AnsiColor(0, 128, 128), AnsiColor(192, 192, 192),
            AnsiColor(128, 128, 128), AnsiColor(255, 0, 0),
            AnsiColor(0, 255, 0), AnsiColor(255, 255, 0),
            AnsiColor(0, 0, 255), AnsiColor(255, 0, 255),
            AnsiColor(0, 255, 255), AnsiColor(255, 255, 255),
        ]
        return standard_colors[index] if index < len(standard_colors) else DEFAULT_FG

    if index < 232:
        i = index - 16
        r = i // 36
        g = (i % 36) // 6
        b = i % 6
        return AnsiColor(
            r=0 if r == 0 else 55 + r * 40,
            g=0 if g == 0 else 55 + g * 40,
            b=0 if b == 0 else 55 + b * 40,
        )

    gray = (index - 232) * 10 + 8
    return AnsiColor(gray, gray, gray)


def parse_ansi(text: str) -> list[ParsedLine]:
    """
    Parse ANSI escape sequences from text.
    Supports basic colors, 256-color mode, and 24-bit true color.
    """
    lines: list[ParsedLine] = []
    raw_lines = text.split("\n")

    for line in raw_lines:
        spans: list[TextSpan] = []
        current_color = DEFAULT_FG
        bold = False
        i = 0

        while i < len(line):
            if line[i] == "\x1b" and i + 1 < len(line) and line[i + 1] == "[":
                j = i + 2
                while j < len(line) and not line[j].isalpha():
                    j += 1

                if j < len(line) and line[j] == "m":
                    codes_str = line[i + 2 : j]
                    codes = [int(c) for c in codes_str.split(";") if c] if codes_str else [0]

                    k = 0
                    while k < len(codes):
                        code = codes[k]
                        if code == 0:
                            current_color = DEFAULT_FG
                            bold = False
                        elif code == 1:
                            bold = True
                        elif 30 <= code <= 37:
                            current_color = ANSI_COLORS.get(code, DEFAULT_FG)
                        elif 90 <= code <= 97:
                            current_color = ANSI_COLORS.get(code, DEFAULT_FG)
                        elif code == 39:
                            current_color = DEFAULT_FG
                        elif code == 38:
                            if k + 2 < len(codes) and codes[k + 1] == 5:
                                current_color = _get_256_color(codes[k + 2])
                                k += 2
                            elif (
                                k + 4 < len(codes) and codes[k + 1] == 2
                            ):
                                current_color = AnsiColor(
                                    r=codes[k + 2],
                                    g=codes[k + 3],
                                    b=codes[k + 4],
                                )
                                k += 4
                        k += 1

                i = j + 1
                continue

            text_start = i
            while i < len(line) and line[i] != "\x1b":
                i += 1

            span_text = line[text_start:i]
            if span_text:
                spans.append(TextSpan(text=span_text, color=current_color, bold=bold))

        if not spans:
            spans.append(TextSpan(text="", color=DEFAULT_FG, bold=False))

        lines.append(spans)

    return lines


@dataclass
class AnsiToSvgOptions:
    font_family: str = "Menlo, Monaco, monospace"
    font_size: int = 14
    line_height: int = 22
    padding_x: int = 24
    padding_y: int = 24
    background_color: Optional[str] = None
    border_radius: int = 8


def ansi_to_svg(ansi_text: str, options: Optional[AnsiToSvgOptions] = None) -> str:
    """Convert ANSI text to SVG."""
    opts = options or AnsiToSvgOptions()
    bg_color = opts.background_color or f"rgb({DEFAULT_BG.r}, {DEFAULT_BG.g}, {DEFAULT_BG.b})"

    lines = parse_ansi(ansi_text)

    # Trim trailing empty lines
    while lines and all(span.text.strip() == "" for span in lines[-1]):
        lines.pop()

    char_width_estimate = opts.font_size * 0.6
    max_line_length = max(
        (sum(len(s.text) for s in spans) for spans in lines), default=0
    )
    width = int(max_line_length * char_width_estimate + opts.padding_x * 2)
    height = len(lines) * opts.line_height + opts.padding_y * 2

    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">\n'
    svg += f'  <rect width="100%" height="100%" fill="{bg_color}" rx="{opts.border_radius}" ry="{opts.border_radius}"/>\n'
    svg += f"  <style>\n"
    svg += f"    text {{ font-family: {opts.font_family}; font-size: {opts.font_size}px; white-space: pre; }}\n"
    svg += f"    .b {{ font-weight: bold; }}\n"
    svg += f"  </style>\n"

    for line_index, spans in enumerate(lines):
        y = opts.padding_y + (line_index + 1) * opts.line_height - (opts.line_height - opts.font_size) / 2
        svg += f'  <text x="{opts.padding_x}" y="{y}" xml:space="preserve">'

        for span in spans:
            if not span.text:
                continue
            color_str = f"rgb({span.color.r}, {span.color.g}, {span.color.b})"
            bold_class = ' class="b"' if span.bold else ""
            svg += f'<tspan fill="{color_str}"{bold_class}>{escape_xml(span.text)}</tspan>'

        svg += "</text>\n"

    svg += "</svg>"
    return svg
