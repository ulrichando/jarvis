"""Measure the dimensions of a DOM element."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ElementDimensions:
    width: int = 0
    height: int = 0


def measure_element(node: Any) -> ElementDimensions:
    """Measure the dimensions of a particular Box element."""
    yoga = getattr(node, "yoga_node", None)
    if yoga is None:
        return ElementDimensions(width=0, height=0)
    return ElementDimensions(
        width=yoga.get_computed_width() if hasattr(yoga, "get_computed_width") else 0,
        height=yoga.get_computed_height() if hasattr(yoga, "get_computed_height") else 0,
    )
