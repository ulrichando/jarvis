"""useInput hook - handle keyboard input."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable

from ..events.input_event import Key


@dataclass
class UseInputOptions:
    is_active: bool = True


InputHandler = Callable[[str, Key], None]


class UseInput:
    """Keyboard input handler. In TS this is a React hook; here it's a class."""

    def __init__(self, handler: InputHandler, options: UseInputOptions | None = None) -> None:
        self.handler = handler
        self.options = options or UseInputOptions()
        self._active = self.options.is_active

    def handle(self, input_: str, key: Key) -> None:
        if self._active:
            self.handler(input_, key)

    def set_active(self, active: bool) -> None:
        self._active = active
