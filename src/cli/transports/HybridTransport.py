"""Hybrid transport -- combines WebSocket and SSE transports."""

from __future__ import annotations

from typing import Any, Callable, Optional


class HybridTransport:
    """Combines WebSocket (inbound) and SSE (outbound) transports."""

    def __init__(self, ws_url: str, sse_url: str, access_token: str) -> None:
        self._ws_url = ws_url
        self._sse_url = sse_url
        self._access_token = access_token
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self._connected = True

    def write(self, event: dict[str, Any]) -> None:
        pass

    async def close(self) -> None:
        self._connected = False
