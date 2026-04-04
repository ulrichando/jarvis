"""Session runner -- spawns and manages child CLI processes for bridge sessions."""

from __future__ import annotations

import asyncio
import logging
import subprocess
from typing import Any, Callable, Optional

from .types import SessionActivity, SessionHandle, SessionSpawnOpts

logger = logging.getLogger(__name__)


class SessionRunner:
    """Spawns and manages child CLI processes for bridge sessions."""

    def spawn(self, opts: SessionSpawnOpts, dir_path: str) -> SessionHandle:
        """Spawn a child CLI process for a bridge session."""
        logger.debug("[session-runner] Spawning session %s in %s", opts.session_id, dir_path)

        done_future = asyncio.get_event_loop().create_future()

        handle = SessionHandle(
            session_id=opts.session_id,
            done=done_future,
            access_token=opts.access_token,
        )

        return handle


def create_session_spawner() -> SessionRunner:
    """Create a session spawner."""
    return SessionRunner()
