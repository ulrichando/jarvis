"""Spinner utility functions: color interpolation and character sets."""

from __future__ import annotations

from typing import Optional


def getDefaultCharacters() -> list[str]:
    """Get the default spinner character set (braille)."""
    return list("\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f")


def hueToRgb(p: float, q: float, t: float) -> float:
    """Convert hue component to RGB value (HSL helper)."""
    if t < 0:
        t += 1
    if t > 1:
        t -= 1
    if t < 1 / 6:
        return p + (q - p) * 6 * t
    if t < 1 / 2:
        return q
    if t < 2 / 3:
        return p + (q - p) * (2 / 3 - t) * 6
    return p


def toRGBColor(h: float, s: float, l: float) -> tuple[int, int, int]:
    """Convert HSL (0-1 range) to RGB (0-255 range)."""
    if s == 0:
        val = int(l * 255)
        return (val, val, val)

    q = l * (1 + s) if l < 0.5 else l + s - l * s
    p = 2 * l - q

    r = hueToRgb(p, q, h + 1 / 3)
    g = hueToRgb(p, q, h)
    b = hueToRgb(p, q, h - 1 / 3)

    return (int(r * 255), int(g * 255), int(b * 255))


def parseRGB(color_str: str) -> Optional[tuple[int, int, int]]:
    """Parse an RGB color string like '#ff0000' or 'rgb(255,0,0)'.

    Returns (r, g, b) tuple or None if unparseable.
    """
    color_str = color_str.strip()

    # Hex format
    if color_str.startswith("#"):
        hex_str = color_str[1:]
        if len(hex_str) == 3:
            hex_str = "".join(c * 2 for c in hex_str)
        if len(hex_str) == 6:
            try:
                r = int(hex_str[0:2], 16)
                g = int(hex_str[2:4], 16)
                b = int(hex_str[4:6], 16)
                return (r, g, b)
            except ValueError:
                return None

    # rgb() format
    import re
    match = re.match(r'rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)', color_str)
    if match:
        return (int(match.group(1)), int(match.group(2)), int(match.group(3)))

    return None


def interpolateColor(
    color1: tuple[int, int, int],
    color2: tuple[int, int, int],
    t: float,
) -> tuple[int, int, int]:
    """Linearly interpolate between two RGB colors.

    Args:
        color1: Start color (r, g, b).
        color2: End color (r, g, b).
        t: Interpolation factor (0.0 = color1, 1.0 = color2).

    Returns:
        Interpolated (r, g, b) tuple.
    """
    t = max(0.0, min(1.0, t))
    r = int(color1[0] + (color2[0] - color1[0]) * t)
    g = int(color1[1] + (color2[1] - color1[1]) * t)
    b = int(color1[2] + (color2[2] - color1[2]) * t)
    return (r, g, b)


def rgb_to_ansi(r: int, g: int, b: int) -> str:
    """Convert RGB to ANSI 24-bit color escape sequence."""
    return f"\033[38;2;{r};{g};{b}m"
