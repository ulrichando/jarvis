"""Local main session task -- primary user-facing session."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class LocalMainSessionTask:
    """The main interactive session task."""
    session_id: str = ""
    status: str = "idle"

    async def start(self) -> None:
        self.status = "running"

    async def stop(self) -> None:
        self.status = "stopped"
