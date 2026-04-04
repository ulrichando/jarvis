"""Teammate shutdown notification."""

from __future__ import annotations

from typing import Callable, Optional


def check_teammate_shutdown(
    add_notification: Optional[Callable] = None,
    shutdown_teammate: Optional[str] = None,
) -> None:
    """Show notification when a teammate shuts down.

    Equivalent to useTeammateShutdownNotification React hook.
    """
    if not shutdown_teammate or not add_notification:
        return
    add_notification(
        key=f"teammate-shutdown-{shutdown_teammate}",
        text=f"Teammate '{shutdown_teammate}' has shut down",
        priority="immediate",
        timeout_ms=5000,
    )
