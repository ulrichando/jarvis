"""Remote session manager for managing JARVIS remote sessions.

Tracks remote client sessions connected via WebSocket or the bridge API.
Each session has an ID, config, WebSocket reference, and lifecycle state.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import aiohttp.web

logger = logging.getLogger(__name__)


@dataclass
class RemoteSession:
    """A single remote client session."""
    session_id: str
    config: dict[str, Any]
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    ws: Optional[aiohttp.web.WebSocketResponse] = None
    status: str = "connected"  # connected, disconnected, expired

    def touch(self) -> None:
        self.last_active = time.time()

    @property
    def is_alive(self) -> bool:
        return self.status == "connected" and self.ws is not None and not self.ws.closed

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "status": self.status,
            "created_at": self.created_at,
            "last_active": self.last_active,
            "alive": self.is_alive,
        }


class RemoteSessionManager:
    """Manages remote JARVIS sessions.

    Provides session lifecycle (create, query, stop, disconnect) and
    tracks connected WebSocket clients for message broadcasting.
    """

    def __init__(self, max_sessions: int = 5) -> None:
        self._sessions: dict[str, RemoteSession] = {}
        self._max_sessions = max_sessions
        self._connected = False

    # -- connection state ---------------------------------------------------

    def is_connected(self) -> bool:
        """True if the remote bridge is active (at least one session alive)."""
        if not self._connected:
            return False
        return any(s.is_alive for s in self._sessions.values())

    @property
    def active_count(self) -> int:
        return sum(1 for s in self._sessions.values() if s.is_alive)

    def set_connected(self, connected: bool) -> None:
        self._connected = connected

    # -- session lifecycle --------------------------------------------------

    async def create_session(
        self,
        config: dict[str, Any],
        ws: Optional[aiohttp.web.WebSocketResponse] = None,
        session_id: Optional[str] = None,
    ) -> RemoteSession:
        """Create a new remote session. Returns the session object."""
        if self.active_count >= self._max_sessions:
            # Evict oldest inactive session
            self._evict_oldest()

        sid = session_id or str(uuid.uuid4())
        session = RemoteSession(session_id=sid, config=config, ws=ws)
        self._sessions[sid] = session
        self._connected = True
        logger.info("[remote] Session created: %s", sid)
        return session

    async def stop_session(self, session_id: str) -> bool:
        """Stop and remove a session."""
        session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        session.status = "disconnected"
        if session.ws and not session.ws.closed:
            await session.ws.close()
        logger.info("[remote] Session stopped: %s", session_id)
        return True

    def get_session(self, session_id: str) -> Optional[RemoteSession]:
        return self._sessions.get(session_id)

    def list_sessions(self) -> list[str]:
        return list(self._sessions.keys())

    def list_session_info(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self._sessions.values()]

    async def disconnect(self) -> None:
        """Disconnect all sessions and shut down the remote bridge."""
        for sid in list(self._sessions.keys()):
            await self.stop_session(sid)
        self._connected = False
        logger.info("[remote] All sessions disconnected")

    async def broadcast(self, data: dict[str, Any]) -> int:
        """Send a message to all connected remote sessions. Returns count sent."""
        sent = 0
        for session in self._sessions.values():
            if session.is_alive:
                try:
                    await session.ws.send_json(data)
                    sent += 1
                except Exception:
                    session.status = "disconnected"
        return sent

    def _evict_oldest(self) -> None:
        """Remove the oldest inactive session to make room."""
        inactive = [
            (sid, s) for sid, s in self._sessions.items()
            if not s.is_alive
        ]
        if inactive:
            inactive.sort(key=lambda x: x[1].last_active)
            oldest_id = inactive[0][0]
            self._sessions.pop(oldest_id, None)
            logger.debug("[remote] Evicted session: %s", oldest_id)
