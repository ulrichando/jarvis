"""Remote I/O for bridge/SDK communication."""

from __future__ import annotations

from typing import Any, Callable, Optional


class RemoteIO:
    """Handles remote I/O for bridge sessions."""

    def __init__(self, write: Optional[Callable[[dict], None]] = None) -> None:
        self._write = write

    def send(self, event: dict[str, Any]) -> None:
        """Send an event to the remote."""
        if self._write:
            self._write(event)
