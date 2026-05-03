"""Python SDK for the JARVIS event hub.

Single class HubClient with publish/subscribe/read methods. Designed
for use by voice-agent, the hub daemon itself, the log analyzer, and
the memory recall subagent. Web (server-side) uses the parallel
client.ts.

Connection: pass an existing aioredis client OR call
`HubClient.from_url(...)`. Reads against state.db are static methods
that don't require a Redis connection.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

EVENTS_STREAM = "events:conversation"


def _new_event_id() -> str:
    """Source-side event id. uuid4 hex — uniqueness is what matters
    for idempotency; the timestamp is in the envelope separately."""
    return uuid.uuid4().hex


def _state_db_path() -> Path:
    return Path(os.environ.get(
        "JARVIS_HUB_DB",
        str(Path.home() / ".jarvis" / "hub" / "state.db"),
    ))


class _ReadMixin:
    """Synchronous reads against state.db. SQLite WAL mode makes
    concurrent reader access safe; no need to round-trip Redis for
    queries the daemon has already materialized."""

    @staticmethod
    def read_recent_sync(
        db_path: Path | str | None = None,
        limit: int = 8,
    ) -> list[tuple[str, str]]:
        """Return last `limit` (role, text) pairs across all sessions,
        newest-first. Returns [] if state.db doesn't exist yet."""
        path = Path(db_path) if db_path else _state_db_path()
        if not path.exists():
            return []
        conn = sqlite3.connect(str(path))
        try:
            return conn.execute(
                "SELECT role, text FROM messages "
                "ORDER BY ts DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            conn.close()

    @staticmethod
    def read_session_sync(
        session_id: str,
        db_path: Path | str | None = None,
        limit: int = 100,
    ) -> list[tuple[str, str]]:
        """Return up to `limit` (role, text) pairs for a session,
        oldest-first."""
        path = Path(db_path) if db_path else _state_db_path()
        if not path.exists():
            return []
        conn = sqlite3.connect(str(path))
        try:
            return conn.execute(
                "SELECT role, text FROM messages "
                "WHERE session_id = ? ORDER BY ts ASC, id ASC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        finally:
            conn.close()


class HubClient(_ReadMixin):
    """Thin wrapper over an aioredis connection.

    Caller owns the redis instance unless `from_url` was used. The
    SDK doesn't open or close it under those circumstances either —
    explicit lifetime via `await redis.aclose()` if needed.
    """

    OFFLINE_MAX = 100

    def __init__(self, redis: Any, source: str):
        if not source:
            raise ValueError("source is required (voice|web|cli|phone|...)")
        self._redis = redis
        self._source = source
        self._offline: deque[dict] = deque(maxlen=self.OFFLINE_MAX)

    @classmethod
    def from_url(cls, source: str, url: str | None = None) -> "HubClient":
        import redis.asyncio as aredis
        url = url or os.environ.get(
            "JARVIS_HUB_URL", "redis://127.0.0.1:6379"
        )
        return cls(
            redis=aredis.from_url(url, decode_responses=True),
            source=source,
        )

    async def publish(
        self,
        type: str,
        session_id: str,
        payload: dict | None = None,
    ) -> str:
        """Publish an event. Returns the source_event_id.

        Buffers up to OFFLINE_MAX events in-memory if Redis is
        unreachable; flush via `flush_offline_queue()`.
        """
        eid = _new_event_id()
        evt = {
            "source": self._source,
            "source_event_id": eid,
            "type": type,
            "session_id": session_id,
            "source_ts": int(time.time() * 1000),
            "payload": payload or {},
        }
        record = {"data": json.dumps(evt)}
        try:
            await self._redis.xadd(EVENTS_STREAM, record)
        except Exception:
            self._offline.append(record)
        return eid

    async def flush_offline_queue(self) -> int:
        """Re-send any buffered events. Returns count flushed.
        Stops on first failure; remaining events stay queued."""
        flushed = 0
        while self._offline:
            record = self._offline[0]
            try:
                await self._redis.xadd(EVENTS_STREAM, record)
            except Exception:
                break
            self._offline.popleft()
            flushed += 1
        return flushed
