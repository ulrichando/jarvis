"""CCR v2 client for session communication."""

from __future__ import annotations

from typing import Any, Callable, Optional


class CCRClient:
    """Client for CCR v2 /worker/* endpoints."""

    def __init__(self, session_url: str, access_token: str, worker_epoch: int) -> None:
        self._session_url = session_url
        self._access_token = access_token
        self._worker_epoch = worker_epoch

    async def heartbeat(self) -> dict[str, Any]:
        return {"status": "ok"}

    async def send_events(self, events: list[dict]) -> None:
        pass

    async def update_state(self, state: str, summary: str = "") -> None:
        pass

    def update_access_token(self, token: str) -> None:
        self._access_token = token
