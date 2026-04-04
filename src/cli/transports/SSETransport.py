"""SSE (Server-Sent Events) transport for CCR v2 sessions."""

from __future__ import annotations

from typing import Any, Callable, Optional


class SSETransport:
    """SSE-based transport for receiving events from CCR v2."""

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

    async def connect(self) -> None:
        self._connected = True
        if self._on_connect:
            self._on_connect()

    def write(self, event: dict[str, Any]) -> None:
        pass

    async def close(self) -> None:
        self._connected = False
