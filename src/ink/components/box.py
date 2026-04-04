"""Box component - the fundamental layout primitive."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BoxProps:
    """Properties for the Box component."""
    # Layout
    width: int | str | None = None
    height: int | str | None = None
    min_width: int | str | None = None
    min_height: int | str | None = None
    max_width: int | str | None = None
    max_height: int | str | None = None
    flex_grow: int | None = None
    flex_shrink: int | None = None
    flex_direction: str | None = None  # 'row' | 'column' | 'row-reverse' | 'column-reverse'
    flex_basis: int | str | None = None
    flex_wrap: str | None = None
    align_items: str | None = None
    align_self: str | None = None
    justify_content: str | None = None
    # Spacing
    margin: int | None = None
    margin_x: int | None = None
    margin_y: int | None = None
    margin_top: int | None = None
    margin_bottom: int | None = None
    margin_left: int | None = None
    margin_right: int | None = None
    padding: int | None = None
    padding_x: int | None = None
    padding_y: int | None = None
    padding_top: int | None = None
    padding_bottom: int | None = None
    padding_left: int | None = None
    padding_right: int | None = None
    gap: int | None = None
    column_gap: int | None = None
    row_gap: int | None = None
    # Visual
    border_style: str | None = None
    border_color: str | None = None
    background_color: str | None = None
    display: str | None = None
    overflow: str | None = None
    position: str | None = None


class Box:
    """Layout container element. In TS this renders an ink-box DOM element."""

    def __init__(self, props: BoxProps | None = None) -> None:
        self.props = props or BoxProps()
