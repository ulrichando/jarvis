"""REPL bridge for inter-process communication."""

from __future__ import annotations

from typing import Any, Callable, Optional


class ReplBridge:
    """Bridge for REPL inter-process communication.

    Equivalent to useReplBridge React hook.
    """

    def __init__(
        self,
        on_message: Optional[Callable] = None,
        enabled: bool = False,
    ):
        self._on_message = on_message
        self._enabled = enabled
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self) -> None:
        if not self._enabled:
            return
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def send(self, message: Any) -> bool:
        if not self._connected:
            return False
        return True
