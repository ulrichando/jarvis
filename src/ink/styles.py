"""Style types and application functions for layout nodes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Union

# Color types
Color = str  # RGBColor | HexColor | Ansi256Color | AnsiColor


@dataclass
class TextStyles:
    """Structured text styling properties."""
    color: str | None = None
    background_color: str | None = None
    dim: bool = False
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strikethrough: bool = False
    inverse: bool = False


@dataclass
class Styles:
    """Layout and visual style properties."""
    text_wrap: str | None = None
    position: str | None = None  # 'absolute' | 'relative'
    top: int | str | None = None
    bottom: int | str | None = None
    left: int | str | None = None
    right: int | str | None = None
    column_gap: int | None = None
    row_gap: int | None = None
    gap: int | None = None
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
    flex_grow: int | None = None
    flex_shrink: int | None = None
    flex_direction: str | None = None
    flex_basis: int | str | None = None
    flex_wrap: str | None = None
    align_items: str | None = None
    align_self: str | None = None
    justify_content: str | None = None
    width: int | str | None = None
    height: int | str | None = None
    min_width: int | str | None = None
    min_height: int | str | None = None
    max_width: int | str | None = None
    max_height: int | str | None = None
    display: str | None = None  # 'flex' | 'none'
    border_style: str | None = None
    border_top: bool | None = None
    border_bottom: bool | None = None
    border_left: bool | None = None
    border_right: bool | None = None
    border_color: str | None = None
    border_top_color: str | None = None
    border_bottom_color: str | None = None
    border_left_color: str | None = None
    border_right_color: str | None = None
    border_dim_color: bool = False
    background_color: str | None = None
    opaque: bool = False
    overflow: str | None = None
    overflow_x: str | None = None
    overflow_y: str | None = None
    no_select: bool | str | None = None


def apply_styles(node: Any, style: dict[str, Any] | None = None, resolved_style: dict[str, Any] | None = None) -> None:
    """Apply all style categories to a layout node."""
    if style is None:
        style = {}
    _apply_position_styles(node, style)
    _apply_overflow_styles(node, style)
    _apply_margin_styles(node, style)
    _apply_padding_styles(node, style)
    _apply_flex_styles(node, style)
    _apply_dimension_styles(node, style)
    _apply_display_styles(node, style)
    _apply_border_styles(node, style, resolved_style)
    _apply_gap_styles(node, style)


def _apply_position_styles(node: Any, style: dict[str, Any]) -> None:
    if "position" in style:
        pos_type = "absolute" if style["position"] == "absolute" else "relative"
        node.set_position_type(pos_type)
    for edge in ("top", "bottom", "left", "right"):
        if edge in style:
            v = style[edge]
            if isinstance(v, str):
                node.set_position_percent(edge, int(v.rstrip("%")))
            elif isinstance(v, (int, float)):
                node.set_position(edge, v)
            else:
                node.set_position(edge, float("nan"))


def _apply_overflow_styles(node: Any, style: dict[str, Any]) -> None:
    y = style.get("overflow_y", style.get("overflow"))
    x = style.get("overflow_x", style.get("overflow"))
    if y == "scroll" or x == "scroll":
        node.set_overflow("scroll")
    elif y == "hidden" or x == "hidden":
        node.set_overflow("hidden")
    elif any(k in style for k in ("overflow", "overflow_x", "overflow_y")):
        node.set_overflow("visible")


def _apply_margin_styles(node: Any, style: dict[str, Any]) -> None:
    if "margin" in style:
        node.set_margin("all", style.get("margin", 0))
    if "margin_x" in style:
        node.set_margin("horizontal", style.get("margin_x", 0))
    if "margin_y" in style:
        node.set_margin("vertical", style.get("margin_y", 0))
    for edge in ("left", "right", "top", "bottom"):
        key = f"margin_{edge}"
        if key in style:
            node.set_margin(edge, style.get(key) or 0)


def _apply_padding_styles(node: Any, style: dict[str, Any]) -> None:
    if "padding" in style:
        node.set_padding("all", style.get("padding", 0))
    if "padding_x" in style:
        node.set_padding("horizontal", style.get("padding_x", 0))
    if "padding_y" in style:
        node.set_padding("vertical", style.get("padding_y", 0))
    for edge in ("left", "right", "top", "bottom"):
        key = f"padding_{edge}"
        if key in style:
            node.set_padding(edge, style.get(key) or 0)


def _apply_flex_styles(node: Any, style: dict[str, Any]) -> None:
    if "flex_grow" in style:
        node.set_flex_grow(style.get("flex_grow", 0))
    if "flex_shrink" in style:
        v = style.get("flex_shrink")
        node.set_flex_shrink(v if isinstance(v, (int, float)) else 1)
    if "flex_wrap" in style:
        node.set_flex_wrap(style["flex_wrap"])
    if "flex_direction" in style:
        node.set_flex_direction(style["flex_direction"])
    if "flex_basis" in style:
        v = style["flex_basis"]
        if isinstance(v, (int, float)):
            node.set_flex_basis(v)
        elif isinstance(v, str):
            node.set_flex_basis_percent(int(v.rstrip("%")))
    if "align_items" in style:
        node.set_align_items(style.get("align_items", "stretch"))
    if "align_self" in style:
        node.set_align_self(style.get("align_self", "auto"))
    if "justify_content" in style:
        node.set_justify_content(style.get("justify_content", "flex-start"))


def _apply_dimension_styles(node: Any, style: dict[str, Any]) -> None:
    for prop in ("width", "height", "min_width", "min_height", "max_width", "max_height"):
        if prop in style:
            v = style[prop]
            setter = getattr(node, f"set_{prop}", None)
            if setter:
                if isinstance(v, (int, float)):
                    setter(v)
                elif isinstance(v, str):
                    percent_setter = getattr(node, f"set_{prop}_percent", None)
                    if percent_setter:
                        percent_setter(int(v.rstrip("%")))


def _apply_display_styles(node: Any, style: dict[str, Any]) -> None:
    if "display" in style:
        node.set_display("flex" if style["display"] == "flex" else "none")


def _apply_border_styles(node: Any, style: dict[str, Any], resolved_style: dict[str, Any] | None = None) -> None:
    resolved = resolved_style or style
    if "border_style" in style:
        border_width = 1 if style["border_style"] else 0
        for edge in ("top", "bottom", "left", "right"):
            key = f"border_{edge}"
            node.set_border(edge, border_width if resolved.get(key) is not False else 0)
    else:
        for edge in ("top", "bottom", "left", "right"):
            key = f"border_{edge}"
            if key in style and style[key] is not None:
                node.set_border(edge, 0 if style[key] is False else 1)


def _apply_gap_styles(node: Any, style: dict[str, Any]) -> None:
    if "gap" in style:
        node.set_gap("all", style.get("gap", 0))
    if "column_gap" in style:
        node.set_gap("column", style.get("column_gap", 0))
    if "row_gap" in style:
        node.set_gap("row", style.get("row_gap", 0))
