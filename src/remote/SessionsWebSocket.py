"""WebSocket connection manager for remote sessions."""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class SessionsWebSocket:
    """Manages WebSocket connections for remote sessions."""

    def __init__(self, url: str, access_token: str) -> None:
        self._url = url
        self._access_token = access_token
        self._connected = False
        self._on_message: Optional[Callable] = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    def on_message(self, handler: Callable) -> None:
        self._on_message = handler

    async def send(self, data: dict[str, Any]) -> None:
        pass
