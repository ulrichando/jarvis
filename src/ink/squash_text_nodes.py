"""Squash text nodes into styled segments or plain strings."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StyledSegment:
    """A segment of text with associated styles."""
    text: str = ""
    styles: dict[str, Any] = field(default_factory=dict)
    hyperlink: str | None = None


def squash_text_nodes_to_segments(
    node: Any,
    inherited_styles: dict[str, Any] | None = None,
    inherited_hyperlink: str | None = None,
    out: list[StyledSegment] | None = None,
) -> list[StyledSegment]:
    """Squash text nodes into styled segments, propagating styles down the tree."""
    if inherited_styles is None:
        inherited_styles = {}
    if out is None:
        out = []

    merged_styles = {**inherited_styles, **(node.text_styles or {})} if getattr(node, "text_styles", None) else inherited_styles

    for child_node in getattr(node, "child_nodes", []):
        if child_node is None:
            continue

        if child_node.node_name == "#text":
            if len(child_node.node_value) > 0:
                out.append(StyledSegment(
                    text=child_node.node_value,
                    styles=merged_styles,
                    hyperlink=inherited_hyperlink,
                ))
        elif child_node.node_name in ("ink-text", "ink-virtual-text"):
            squash_text_nodes_to_segments(child_node, merged_styles, inherited_hyperlink, out)
        elif child_node.node_name == "ink-link":
            href = child_node.attributes.get("href")
            squash_text_nodes_to_segments(
                child_node, merged_styles, href or inherited_hyperlink, out
            )

    return out


def squash_text_nodes(node: Any) -> str:
    """Squash text nodes into a plain string (without styles)."""
    text = ""

    for child_node in getattr(node, "child_nodes", []):
        if child_node is None:
            continue

        if child_node.node_name == "#text":
            text += child_node.node_value
        elif child_node.node_name in ("ink-text", "ink-virtual-text", "ink-link"):
            text += squash_text_nodes(child_node)

    return text
