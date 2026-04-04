"""DOM-like focus manager for the Ink terminal UI."""

from __future__ import annotations

from typing import Any, Callable

from .dom import DOMElement
from .events.focus_event import FocusEvent

MAX_FOCUS_STACK = 32


class FocusManager:
    """Pure state focus tracker. Stored on root DOMElement."""

    def __init__(self, dispatch_focus_event: Callable) -> None:
        self.active_element: DOMElement | None = None
        self._dispatch_focus_event = dispatch_focus_event
        self._enabled = True
        self._focus_stack: list[DOMElement] = []

    def focus(self, node: DOMElement) -> None:
        if node is self.active_element:
            return
        if not self._enabled:
            return

        previous = self.active_element
        if previous:
            try:
                idx = self._focus_stack.index(previous)
                self._focus_stack.pop(idx)
            except ValueError:
                pass
            self._focus_stack.append(previous)
            if len(self._focus_stack) > MAX_FOCUS_STACK:
                self._focus_stack.pop(0)
            self._dispatch_focus_event(previous, FocusEvent("blur", node))

        self.active_element = node
        self._dispatch_focus_event(node, FocusEvent("focus", previous))

    def blur(self) -> None:
        if not self.active_element:
            return
        previous = self.active_element
        self.active_element = None
        self._dispatch_focus_event(previous, FocusEvent("blur", None))

    def handle_node_removed(self, node: DOMElement, root: DOMElement) -> None:
        self._focus_stack = [
            n for n in self._focus_stack
            if n is not node and _is_in_tree(n, root)
        ]

        if not self.active_element:
            return
        if self.active_element is not node and _is_in_tree(self.active_element, root):
            return

        removed = self.active_element
        self.active_element = None
        self._dispatch_focus_event(removed, FocusEvent("blur", None))

        while self._focus_stack:
            candidate = self._focus_stack.pop()
            if _is_in_tree(candidate, root):
                self.active_element = candidate
                self._dispatch_focus_event(candidate, FocusEvent("focus", removed))
                return

    def handle_auto_focus(self, node: DOMElement) -> None:
        self.focus(node)

    def handle_click_focus(self, node: DOMElement) -> None:
        tab_index = node.attributes.get("tabIndex")
        if not isinstance(tab_index, int):
            return
        self.focus(node)

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    def focus_next(self, root: DOMElement) -> None:
        self._move_focus(1, root)

    def focus_previous(self, root: DOMElement) -> None:
        self._move_focus(-1, root)

    def _move_focus(self, direction: int, root: DOMElement) -> None:
        if not self._enabled:
            return

        tabbable = _collect_tabbable(root)
        if not tabbable:
            return

        current_index = -1
        if self.active_element:
            try:
                current_index = tabbable.index(self.active_element)
            except ValueError:
                current_index = -1

        if current_index == -1:
            next_index = 0 if direction == 1 else len(tabbable) - 1
        else:
            next_index = (current_index + direction) % len(tabbable)

        if 0 <= next_index < len(tabbable):
            self.focus(tabbable[next_index])


def _collect_tabbable(root: DOMElement) -> list[DOMElement]:
    result: list[DOMElement] = []
    _walk_tree(root, result)
    return result


def _walk_tree(node: DOMElement, result: list[DOMElement]) -> None:
    tab_index = node.attributes.get("tabIndex")
    if isinstance(tab_index, int) and tab_index >= 0:
        result.append(node)

    for child in node.child_nodes:
        if hasattr(child, "node_name") and child.node_name != "#text":
            _walk_tree(child, result)


def _is_in_tree(node: DOMElement, root: DOMElement) -> bool:
    current: DOMElement | None = node
    while current:
        if current is root:
            return True
        current = current.parent_node
    return False


def get_root_node(node: DOMElement) -> DOMElement:
    """Walk up to root and return it."""
    current: DOMElement | None = node
    while current:
        if current.focus_manager:
            return current
        current = current.parent_node
    raise RuntimeError("Node is not in a tree with a FocusManager")


def get_focus_manager(node: DOMElement) -> FocusManager:
    """Walk up to root and return its FocusManager."""
    return get_root_node(node).focus_manager
