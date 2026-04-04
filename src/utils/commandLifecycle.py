"""Command lifecycle notification."""

from __future__ import annotations

from typing import Callable, Literal, Optional

CommandLifecycleState = Literal["started", "completed"]
CommandLifecycleListener = Callable[[str, CommandLifecycleState], None]

_listener: Optional[CommandLifecycleListener] = None


def set_command_lifecycle_listener(cb: Optional[CommandLifecycleListener]) -> None:
    global _listener
    _listener = cb


def notify_command_lifecycle(uuid: str, state: CommandLifecycleState) -> None:
    if _listener is not None:
        _listener(uuid, state)
