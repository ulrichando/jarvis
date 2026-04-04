"""Serial batch event uploader for efficient event delivery."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional


class SerialBatchEventUploader:
    """Batches and uploads events serially to avoid overwhelming the server."""

    def __init__(self, upload_fn: Callable[[list[dict]], Any], max_batch_size: int = 50) -> None:
        self._upload_fn = upload_fn
        self._max_batch_size = max_batch_size
        self._queue: list[dict] = []
        self._uploading = False

    def enqueue(self, event: dict[str, Any]) -> None:
        self._queue.append(event)

    async def flush(self) -> None:
        if self._uploading or not self._queue:
            return
        self._uploading = True
        try:
            batch = self._queue[:self._max_batch_size]
            self._queue = self._queue[self._max_batch_size:]
            await self._upload_fn(batch)
        finally:
            self._uploading = False

    async def close(self) -> None:
        while self._queue:
            await self.flush()
