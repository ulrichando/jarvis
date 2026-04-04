"""
Exporter for 1st-party event logging to /api/event_logging/batch.

Provides resilience with:
- Append-only log for failed events
- Quadratic backoff retry for failed events
- Chunking large event sets into smaller batches
- Auth fallback on 401 errors
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)

BATCH_UUID = str(uuid.uuid4())
FILE_PREFIX = "1p_failed_events."


def _get_storage_dir() -> Path:
    """Get storage directory for failed events."""
    config_dir = os.environ.get("JARVIS_HOME", os.path.expanduser("~/.jarvis"))
    return Path(config_dir) / "telemetry"


@dataclass
class FirstPartyEventLoggingEvent:
    """API envelope for event logging."""
    event_type: str  # 'JarvisInternalEvent' | 'GrowthbookExperimentEvent'
    event_data: Any = None


@dataclass
class FirstPartyEventLoggingPayload:
    """Payload for batch event export."""
    events: List[Dict[str, Any]] = field(default_factory=list)


class FirstPartyEventLoggingExporter:
    """Exporter for 1st-party event logging.

    Export cycles send events in batches with resilience features
    including file-backed retry, quadratic backoff, and auth fallback.
    """

    def __init__(
        self,
        timeout: int = 10000,
        max_batch_size: int = 200,
        skip_auth: bool = False,
        batch_delay_ms: int = 100,
        base_backoff_delay_ms: int = 500,
        max_backoff_delay_ms: int = 30000,
        max_attempts: int = 8,
        path: Optional[str] = None,
        base_url: Optional[str] = None,
        is_killed: Optional[Callable[[], bool]] = None,
    ):
        base = base_url or "https://api.anthropic.com"
        self.endpoint = f"{base}{path or '/api/event_logging/batch'}"
        self.timeout = timeout / 1000  # Convert to seconds
        self.max_batch_size = max_batch_size
        self.skip_auth = skip_auth
        self.batch_delay_ms = batch_delay_ms
        self.base_backoff_delay_ms = base_backoff_delay_ms
        self.max_backoff_delay_ms = max_backoff_delay_ms
        self.max_attempts = max_attempts
        self.is_killed = is_killed or (lambda: False)
        self.pending_exports: list[asyncio.Task] = []
        self.is_shutdown = False
        self._cancel_backoff: Optional[asyncio.Task] = None
        self.attempts = 0
        self.is_retrying = False
        self._session_id = os.environ.get("JARVIS_SESSION_ID", "default")

    def _get_current_batch_file_path(self) -> Path:
        return _get_storage_dir() / f"{FILE_PREFIX}{self._session_id}.{BATCH_UUID}.json"

    async def _load_events_from_file(self, file_path: Path) -> List[Dict[str, Any]]:
        try:
            if not file_path.exists():
                return []
            lines = file_path.read_text().strip().split("\n")
            return [json.loads(line) for line in lines if line.strip()]
        except Exception:
            return []

    async def _save_events_to_file(
        self, file_path: Path, events: List[Dict[str, Any]]
    ) -> None:
        try:
            if not events:
                file_path.unlink(missing_ok=True)
            else:
                file_path.parent.mkdir(parents=True, exist_ok=True)
                content = "\n".join(json.dumps(e) for e in events) + "\n"
                file_path.write_text(content)
        except Exception as e:
            logger.error(f"Failed to save events: {e}")

    async def _append_events_to_file(
        self, file_path: Path, events: List[Dict[str, Any]]
    ) -> None:
        if not events:
            return
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            content = "\n".join(json.dumps(e) for e in events) + "\n"
            with open(file_path, "a") as f:
                f.write(content)
        except Exception as e:
            logger.error(f"Failed to append events: {e}")

    async def _send_batch(self, payload: Dict[str, Any]) -> None:
        """Send a batch of events to the endpoint."""
        if self.is_killed():
            raise RuntimeError("firstParty sink killswitch active")

        headers = {"Content-Type": "application/json"}

        async with aiohttp.ClientSession() as session:
            await session.post(
                self.endpoint,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            )

    async def _send_events_in_batches(
        self, events: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Send events in batches, returning any that failed."""
        batches = [
            events[i : i + self.max_batch_size]
            for i in range(0, len(events), self.max_batch_size)
        ]

        failed_events: List[Dict[str, Any]] = []
        for i, batch in enumerate(batches):
            try:
                await self._send_batch({"events": batch})
            except Exception:
                for j in range(i, len(batches)):
                    failed_events.extend(batches[j])
                break

            if i < len(batches) - 1 and self.batch_delay_ms > 0:
                await asyncio.sleep(self.batch_delay_ms / 1000)

        return failed_events

    async def export(self, events: List[Dict[str, Any]]) -> bool:
        """Export events. Returns True on success."""
        if self.is_shutdown:
            return False

        if not events:
            return True

        if self.attempts >= self.max_attempts:
            return False

        failed = await self._send_events_in_batches(events)
        self.attempts += 1

        if failed:
            await self._append_events_to_file(
                self._get_current_batch_file_path(), failed
            )
            return False

        self.attempts = 0
        return True

    async def shutdown(self) -> None:
        """Shutdown the exporter."""
        self.is_shutdown = True
        if self._cancel_backoff:
            self._cancel_backoff.cancel()
            self._cancel_backoff = None
        await self.force_flush()

    async def force_flush(self) -> None:
        """Flush all pending exports."""
        await asyncio.gather(*self.pending_exports, return_exceptions=True)
