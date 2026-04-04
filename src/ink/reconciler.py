"""Reconciler - manages DOM updates (React reconciler equivalent).

In the TS version, this uses react-reconciler to bridge React components
to the Ink DOM. In Python, this is a simplified version that manages
DOM node creation and updates directly.
"""

from __future__ import annotations

from typing import Any, Callable

from .dom import (
    DOMElement,
    TextNode,
    append_child_node,
    create_node,
    create_text_node,
    insert_before_node,
    mark_dirty,
    remove_child_node,
    set_attribute,
    set_style,
    set_text_node_value,
    set_text_styles,
)
from .events.event_handlers import EVENT_HANDLER_PROPS


class Reconciler:
    """Manages DOM tree updates."""

    def __init__(self) -> None:
        self._root: DOMElement | None = None

    def create_container(self) -> DOMElement:
        """Create the root container node."""
        self._root = create_node("ink-root")
        return self._root

    def create_instance(self, type_: str, props: dict[str, Any]) -> DOMElement:
        """Create a new DOM element."""
        node = create_node(type_)
        self._apply_props(node, props)
        return node

    def create_text_instance(self, text: str) -> TextNode:
        """Create a new text node."""
        return create_text_node(text)

    def append_child(self, parent: DOMElement, child: DOMElement) -> None:
        append_child_node(parent, child)

    def remove_child(self, parent: DOMElement, child: Any) -> None:
        remove_child_node(parent, child)

    def insert_before(self, parent: DOMElement, child: Any, before: Any) -> None:
        insert_before_node(parent, child, before)

    def commit_text_update(self, node: TextNode, text: str) -> None:
        set_text_node_value(node, text)

    def commit_update(self, node: DOMElement, props: dict[str, Any]) -> None:
        self._apply_props(node, props)

    def _apply_props(self, node: DOMElement, props: dict[str, Any]) -> None:
        """Apply props to a DOM node."""
        style = props.get("style")
        if style:
            set_style(node, style)

        text_styles = props.get("textStyles")
        if text_styles:
            set_text_styles(node, text_styles)

        event_handlers: dict[str, Any] = {}
        for key, value in props.items():
            if key in ("style", "textStyles", "children"):
                continue
            if key in EVENT_HANDLER_PROPS or key.startswith("on"):
                event_handlers[key] = value
            else:
                set_attribute(node, key, value)

        if event_handlers:
            node._event_handlers = event_handlers
