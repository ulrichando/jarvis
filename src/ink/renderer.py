"""Renderer - creates the render pipeline from DOM to screen."""

from __future__ import annotations

from typing import Any, Callable

from .dom import DOMElement
from .frame import Frame
from .screen import Screen, StylePool, create_screen


class Renderer:
    """Creates the render pipeline from DOM tree to screen buffer."""

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.style_pool = StylePool()

    def render(self, root: DOMElement) -> Frame:
        """Render the DOM tree to a Frame."""
        screen = create_screen(self.width, self.height, self.style_pool)

        from .render_node_to_output import render_node_to_output
        scroll_hint = render_node_to_output(root, screen)

        from .frame import Cursor, Size
        return Frame(
            screen=screen,
            viewport=Size(width=self.width, height=self.height),
            cursor=Cursor(x=0, y=self.height, visible=True),
            scroll_hint=scroll_hint,
        )

    def resize(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
