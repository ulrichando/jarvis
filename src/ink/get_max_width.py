"""Get the content width of a yoga node (computed width minus padding and border)."""

from __future__ import annotations

from typing import Any


def get_max_width(yoga_node: Any) -> int:
    """Returns the yoga node's content width."""
    return (
        yoga_node.get_computed_width()
        - yoga_node.get_computed_padding("left")
        - yoga_node.get_computed_padding("right")
        - yoga_node.get_computed_border("left")
        - yoga_node.get_computed_border("right")
    )
