"""Color difference calculation utilities."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class RGB:
    r: int
    g: int
    b: int


@dataclass
class Lab:
    l: float
    a: float
    b: float


def rgb_to_lab(color: RGB) -> Lab:
    """Convert RGB to CIE-Lab color space."""
    r = color.r / 255.0
    g = color.g / 255.0
    b = color.b / 255.0

    r = ((r + 0.055) / 1.055) ** 2.4 if r > 0.04045 else r / 12.92
    g = ((g + 0.055) / 1.055) ** 2.4 if g > 0.04045 else g / 12.92
    b = ((b + 0.055) / 1.055) ** 2.4 if b > 0.04045 else b / 12.92

    x = (r * 0.4124 + g * 0.3576 + b * 0.1805) / 0.95047
    y = r * 0.2126 + g * 0.7152 + b * 0.0722
    z = (r * 0.0193 + g * 0.1192 + b * 0.9505) / 1.08883

    x = x ** (1/3) if x > 0.008856 else (7.787 * x) + 16/116
    y = y ** (1/3) if y > 0.008856 else (7.787 * y) + 16/116
    z = z ** (1/3) if z > 0.008856 else (7.787 * z) + 16/116

    return Lab(l=(116 * y) - 16, a=500 * (x - y), b=200 * (y - z))


def delta_e(c1: RGB, c2: RGB) -> float:
    """Calculate the CIE76 color difference between two RGB colors."""
    lab1 = rgb_to_lab(c1)
    lab2 = rgb_to_lab(c2)
    return math.sqrt(
        (lab2.l - lab1.l) ** 2 +
        (lab2.a - lab1.a) ** 2 +
        (lab2.b - lab1.b) ** 2
    )
