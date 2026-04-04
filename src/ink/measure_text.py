"""Text measurement for layout calculations."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .line_width_cache import line_width


@dataclass
class MeasureOutput:
    width: int = 0
    height: int = 0


def measure_text(text: str, max_width: int) -> MeasureOutput:
    """Measure text dimensions, accounting for wrapping."""
    if not text:
        return MeasureOutput(width=0, height=0)

    no_wrap = max_width <= 0 or not math.isfinite(max_width)

    height = 0
    width = 0
    start = 0

    while start <= len(text):
        end = text.find("\n", start)
        line = text[start:] if end == -1 else text[start:end]

        w = line_width(line)
        width = max(width, w)

        if no_wrap:
            height += 1
        else:
            height += 1 if w == 0 else math.ceil(w / max_width)

        if end == -1:
            break
        start = end + 1

    return MeasureOutput(width=width, height=height)
