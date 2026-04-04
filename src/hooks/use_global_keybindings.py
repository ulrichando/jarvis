"""Global keybinding handler registration."""

from __future__ import annotations

from typing import Callable, Dict, Optional


class GlobalKeybindings:
    """Registers global keybinding handlers.

    Equivalent to useGlobalKeybindings React component.
    """

    def __init__(self):
        self._handlers: Dict[str, Callable] = {}
        self._active = True

    def register(self, action: str, handler: Callable) -> None:
        self._handlers[action] = handler

    def unregister(self, action: str) -> None:
        self._handlers.pop(action, None)

    def handle(self, action: str) -> bool:
        if not self._active or action not in self._handlers:
            return False
        self._handlers[action]()
        return True

    def set_active(self, active: bool) -> None:
        self._active = active
