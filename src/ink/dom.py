"""DOM node types and manipulation functions for the Ink terminal UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

ElementNames = Literal[
    "ink-root", "ink-box", "ink-text", "ink-virtual-text",
    "ink-link", "ink-progress", "ink-raw-ansi",
]
TextName = Literal["#text"]
NodeNames = str  # ElementNames | TextName
DOMNodeAttribute = bool | str | int


@dataclass
class DOMElement:
    """A DOM element node."""
    node_name: str = "ink-box"
    attributes: dict[str, DOMNodeAttribute] = field(default_factory=dict)
    child_nodes: list[Any] = field(default_factory=list)
    text_styles: dict[str, Any] | None = None
    parent_node: DOMElement | None = None
    yoga_node: Any = None
    style: dict[str, Any] = field(default_factory=dict)
    dirty: bool = False
    is_hidden: bool = False
    _event_handlers: dict[str, Any] | None = None
    scroll_top: int | None = None
    pending_scroll_delta: int | None = None
    scroll_clamp_min: int | None = None
    scroll_clamp_max: int | None = None
    scroll_height: int | None = None
    scroll_viewport_height: int | None = None
    scroll_viewport_top: int | None = None
    sticky_scroll: bool | None = None
    scroll_anchor: dict[str, Any] | None = None
    focus_manager: Any = None
    on_compute_layout: Callable | None = None
    on_render: Callable | None = None
    on_immediate_render: Callable | None = None
    has_rendered_content: bool = False
    debug_owner_chain: list[str] | None = None


@dataclass
class TextNode:
    """A text node."""
    node_name: str = "#text"
    node_value: str = ""
    parent_node: DOMElement | None = None
    yoga_node: Any = None
    style: dict[str, Any] = field(default_factory=dict)


DOMNode = DOMElement | TextNode


def create_node(node_name: str) -> DOMElement:
    """Create a new DOM element node."""
    node = DOMElement(
        node_name=node_name,
        style={},
        attributes={},
        child_nodes=[],
        parent_node=None,
        dirty=False,
    )
    return node


def append_child_node(node: DOMElement, child_node: DOMElement) -> None:
    """Append a child node to a parent node."""
    if child_node.parent_node:
        remove_child_node(child_node.parent_node, child_node)

    child_node.parent_node = node
    node.child_nodes.append(child_node)
    mark_dirty(node)


def insert_before_node(
    node: DOMElement, new_child: DOMNode, before_child: DOMNode
) -> None:
    """Insert a new child node before an existing child."""
    if new_child.parent_node:
        remove_child_node(new_child.parent_node, new_child)

    new_child.parent_node = node
    try:
        index = node.child_nodes.index(before_child)
        node.child_nodes.insert(index, new_child)
    except ValueError:
        node.child_nodes.append(new_child)

    mark_dirty(node)


def remove_child_node(node: DOMElement, remove_node: DOMNode) -> None:
    """Remove a child node from its parent."""
    remove_node.parent_node = None
    try:
        node.child_nodes.remove(remove_node)
    except ValueError:
        pass
    mark_dirty(node)


def set_attribute(node: DOMElement, key: str, value: DOMNodeAttribute) -> None:
    """Set an attribute on a node."""
    if key == "children":
        return
    if node.attributes.get(key) == value:
        return
    node.attributes[key] = value
    mark_dirty(node)


def set_style(node: DOMNode, style: dict[str, Any]) -> None:
    """Set the style on a node."""
    if node.style == style:
        return
    node.style = style
    mark_dirty(node)


def set_text_styles(node: DOMElement, text_styles: dict[str, Any]) -> None:
    """Set text styles on an element."""
    if node.text_styles == text_styles:
        return
    node.text_styles = text_styles
    mark_dirty(node)


def create_text_node(text: str) -> TextNode:
    """Create a new text node."""
    node = TextNode(node_value=text)
    return node


def mark_dirty(node: DOMNode | None) -> None:
    """Mark a node and all its ancestors as dirty."""
    current = node
    while current is not None:
        if isinstance(current, DOMElement):
            current.dirty = True
        current = current.parent_node


def schedule_render_from(node: DOMNode | None) -> None:
    """Walk to root and call its on_render."""
    cur = node
    while cur and cur.parent_node:
        cur = cur.parent_node
    if cur and isinstance(cur, DOMElement) and cur.on_render:
        cur.on_render()


def set_text_node_value(node: TextNode, text: str) -> None:
    """Set the value of a text node."""
    if not isinstance(text, str):
        text = str(text)
    if node.node_value == text:
        return
    node.node_value = text
    mark_dirty(node)


def clear_yoga_node_references(node: DOMNode) -> None:
    """Clear yogaNode references recursively."""
    if isinstance(node, DOMElement):
        for child in node.child_nodes:
            clear_yoga_node_references(child)
    node.yoga_node = None
