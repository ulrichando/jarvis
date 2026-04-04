"""Root - manages the Ink instance lifecycle."""

from __future__ import annotations

from typing import Any, Callable

from .dom import DOMElement
from .reconciler import Reconciler


class Root:
    """Manages an Ink render tree.

    Creates the container, handles renders, and manages cleanup.
    """

    def __init__(self) -> None:
        self.reconciler = Reconciler()
        self.container = self.reconciler.create_container()
        self._on_render: Callable | None = None

    def render(self, element: Any) -> None:
        """Render an element tree into the container."""
        # In TS this invokes the React reconciler to diff and update
        pass

    def unmount(self) -> None:
        """Unmount the tree and cleanup."""
        self.container.child_nodes.clear()

    def on_render(self, callback: Callable) -> None:
        """Register a render callback."""
        self._on_render = callback
