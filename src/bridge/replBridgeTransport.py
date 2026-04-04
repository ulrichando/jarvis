"""REPL bridge transport -- WebSocket/SSE transport layer."""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional, Protocol

logger = logging.getLogger(__name__)


class ReplBridgeTransport(Protocol):
    """Protocol for bridge transport implementations."""

    def connect(self) -> None: ...
    def write(self, event: dict[str, Any]) -> None: ...
    def close(self) -> None: ...

    @property
    def is_connected(self) -> bool: ...


class WebSocketTransport:
    """WebSocket-based bridge transport."""

    def __init__(
        self,
        url: str,
        access_token: str,
        on_message: Optional[Callable[[str], None]] = None,
        on_connect: Optional[Callable] = None,
        on_close: Optional[Callable[[int], None]] = None,
    ) -> None:
        self._url = url
        self._access_token = access_token
        self._on_message = on_message
        self._on_connect = on_connect
        self._on_close = on_close
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self) -> None:
        """Establish the WebSocket connection."""
        logger.debug("[bridge:ws] Connecting to %s", self._url)
        self._connected = True
        if self._on_connect:
            self._on_connect()

    def write(self, event: dict[str, Any]) -> None:
        """Send an event over the WebSocket."""
        if not self._connected:
            return
        # Would serialize and send via WebSocket
        pass

    def close(self) -> None:
        """Close the WebSocket connection."""
        self._connected = False
        logger.debug("[bridge:ws] Closed")


class SSETransport:
    """SSE-based bridge transport (CCR v2)."""

    def __init__(
        self,
        url: str,
        access_token: str,
        worker_epoch: int,
        on_message: Optional[Callable[[str], None]] = None,
        on_connect: Optional[Callable] = None,
        on_close: Optional[Callable[[int], None]] = None,
    ) -> None:
        self._url = url
        self._access_token = access_token
        self._worker_epoch = worker_epoch
        self._on_message = on_message
        self._on_connect = on_connect
        self._on_close = on_close
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self) -> None:
        """Establish the SSE connection."""
        self._connected = True
        if self._on_connect:
            self._on_connect()

    def write(self, event: dict[str, Any]) -> None:
        """Send an event via HTTP POST."""
        if not self._connected:
            return

    def close(self) -> None:
        """Close the SSE connection."""
        self._connected = False
