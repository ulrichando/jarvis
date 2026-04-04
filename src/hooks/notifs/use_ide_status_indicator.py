"""IDE status indicator notification."""

from __future__ import annotations

from typing import Callable, Optional


def check_ide_status(
    add_notification: Optional[Callable] = None,
    ide_status: Optional[str] = None,
    ide_name: Optional[str] = None,
) -> None:
    """Show IDE connection status in notifications.

    Equivalent to useIDEStatusIndicator React hook.
    """
    if not add_notification or not ide_status:
        return

    name = ide_name or "IDE"
    if ide_status == "connected":
        add_notification(
            key="ide-status",
            text=f"{name} connected",
            priority="low",
        )
    elif ide_status == "disconnected":
        add_notification(
            key="ide-status",
            text=f"{name} disconnected",
            priority="high",
        )
