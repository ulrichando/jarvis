"""Teleport resume session handling."""

from __future__ import annotations

from typing import Any, Callable, Optional


class TeleportResume:
    """Handles resuming a teleported session.

    Equivalent to useTeleportResume React hook.
    """

    def __init__(
        self,
        session_id: Optional[str] = None,
        on_resume: Optional[Callable] = None,
        enabled: bool = False,
    ):
        self._session_id = session_id
        self._on_resume = on_resume
        self._enabled = enabled
        self._resumed = False

    async def resume(self) -> bool:
        if not self._enabled or self._resumed or not self._session_id:
            return False
        self._resumed = True
        if self._on_resume:
            await self._on_resume(self._session_id)
        return True
