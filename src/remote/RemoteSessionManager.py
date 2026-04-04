"""Remote session manager for managing CCR sessions."""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class RemoteSessionManager:
    """Manages remote CCR sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, Any]] = {}

    async def create_session(self, session_id: str, config: dict[str, Any]) -> bool:
        self._sessions[session_id] = config
        return True

    async def stop_session(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None

    def get_session(self, session_id: str) -> Optional[dict[str, Any]]:
        return self._sessions.get(session_id)

    def list_sessions(self) -> list[str]:
        return list(self._sessions.keys())
