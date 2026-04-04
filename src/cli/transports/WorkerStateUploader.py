"""Worker state uploader for CCR v2 sessions."""

from __future__ import annotations

from typing import Any, Optional


class WorkerStateUploader:
    """Uploads worker state to the CCR v2 session."""

    def __init__(self, session_url: str, access_token: str, worker_epoch: int) -> None:
        self._session_url = session_url
        self._access_token = access_token
        self._worker_epoch = worker_epoch

    async def upload_state(self, state: dict[str, Any]) -> None:
        pass

    async def close(self) -> None:
        pass
