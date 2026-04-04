"""Ink - the main entry point for the terminal UI framework.

In the TS version, this is a React component that manages the render loop,
stdin handling, and terminal output. In Python, it's a class with the same
lifecycle logic.
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

from .constants import FRAME_INTERVAL_MS
from .dom import DOMElement
from .frame import Frame, empty_frame
from .reconciler import Reconciler
from .renderer import Renderer
from .root import Root
from .screen import StylePool, CharPool, HyperlinkPool
from .terminal import Terminal


@dataclass
class InkOptions:
    """Options for creating an Ink instance."""
    stdout: Any = None
    stdin: Any = None
    exit_on_ctrl_c: bool = True
    patch_console: bool = True
    debug: bool = False


class Ink:
    """Main Ink instance managing the render loop and terminal output.

    Lifecycle:
    1. Create instance with options
    2. Render elements via render()
    3. Instance manages stdin, stdout, frame scheduling
    4. Unmount via unmount() or waitUntilExit()
    """

    def __init__(self, options: InkOptions | None = None) -> None:
        self.options = options or InkOptions()
        self.terminal = Terminal(
            stdin=self.options.stdin,
            stdout=self.options.stdout or sys.stdout,
        )
        self.root = Root()
        self.renderer = Renderer(
            width=self.terminal.get_size().columns,
            height=self.terminal.get_size().rows,
        )
        self._style_pool = StylePool()
        self._prev_frame: Frame | None = None
        self._exit_promise: asyncio.Future | None = None
        self._unmounted = False

    def render(self, element: Any) -> None:
        """Render an element tree."""
        if self._unmounted:
            return
        self.root.render(element)
        self._schedule_render()

    def _schedule_render(self) -> None:
        """Schedule a render frame."""
        if self._unmounted:
            return
        frame = self.renderer.render(self.root.container)
        self._write_frame(frame)
        self._prev_frame = frame

    def _write_frame(self, frame: Frame) -> None:
        """Write a frame to the terminal."""
        # In the full implementation, this diffs with prev_frame and
        # writes only the changes via LogUpdate
        pass

    def unmount(self) -> None:
        """Unmount the Ink instance and cleanup."""
        self._unmounted = True
        self.root.unmount()

    async def wait_until_exit(self) -> None:
        """Wait until the application exits."""
        if self._exit_promise is None:
            self._exit_promise = asyncio.get_event_loop().create_future()
        await self._exit_promise

    def exit(self, error: Exception | None = None) -> None:
        """Exit the application."""
        self.unmount()
        if self._exit_promise and not self._exit_promise.done():
            if error:
                self._exit_promise.set_exception(error)
            else:
                self._exit_promise.set_result(None)


def render(element: Any, options: InkOptions | None = None) -> Ink:
    """Render an Ink application."""
    instance = Ink(options)
    instance.render(element)
    return instance
