"""Hit testing for mouse events in the terminal UI."""

from __future__ import annotations

from typing import Any

from .dom import DOMElement
from .events.click_event import ClickEvent
from .node_cache import node_cache


def hit_test(node: DOMElement, col: int, row: int) -> DOMElement | None:
    """Find the deepest DOM element whose rendered rect contains (col, row)."""
    node_id = id(node)
    rect = node_cache.get(node_id)
    if not rect:
        return None
    if col < rect.x or col >= rect.x + rect.width or row < rect.y or row >= rect.y + rect.height:
        return None

    # Later siblings paint on top; reversed traversal returns topmost hit
    for i in range(len(node.child_nodes) - 1, -1, -1):
        child = node.child_nodes[i]
        if not isinstance(child, DOMElement) or child.node_name == "#text":
            continue
        hit = hit_test(child, col, row)
        if hit:
            return hit
    return node


def dispatch_click(
    root: DOMElement, col: int, row: int, cell_is_blank: bool = False
) -> bool:
    """Hit-test and bubble a ClickEvent from the deepest node up."""
    target = hit_test(root, col, row)
    if not target:
        return False

    # Click-to-focus
    if root.focus_manager:
        focus_target = target
        while focus_target:
            if isinstance(focus_target.attributes.get("tabIndex"), int):
                root.focus_manager.handle_click_focus(focus_target)
                break
            focus_target = focus_target.parent_node

    event = ClickEvent(col, row, cell_is_blank)
    handled = False
    current: DOMElement | None = target
    while current:
        handlers = current._event_handlers
        if handlers:
            handler = handlers.get("onClick") or handlers.get("on_click")
            if handler:
                handled = True
                rect = node_cache.get(id(current))
                if rect:
                    event.local_col = col - rect.x
                    event.local_row = row - rect.y
                handler(event)
                if event.did_stop_immediate_propagation():
                    return True
        current = current.parent_node
    return handled


def dispatch_hover(
    root: DOMElement, col: int, row: int, hovered: set[DOMElement]
) -> None:
    """Fire onMouseEnter/onMouseLeave as the pointer moves."""
    next_set: set[DOMElement] = set()
    node = hit_test(root, col, row)
    while node:
        handlers = node._event_handlers or {}
        if handlers.get("onMouseEnter") or handlers.get("onMouseLeave") or \
           handlers.get("on_mouse_enter") or handlers.get("on_mouse_leave"):
            next_set.add(node)
        node = node.parent_node

    for old in list(hovered):
        if old not in next_set:
            hovered.discard(old)
            if old.parent_node:
                h = old._event_handlers or {}
                leave = h.get("onMouseLeave") or h.get("on_mouse_leave")
                if leave:
                    leave()

    for n in next_set:
        if n not in hovered:
            hovered.add(n)
            h = n._event_handlers or {}
            enter = h.get("onMouseEnter") or h.get("on_mouse_enter")
            if enter:
                enter()
