"""Auto mode unavailable notification."""

from __future__ import annotations

from typing import Callable, Optional


def check_auto_mode_unavailable(
    add_notification: Optional[Callable] = None,
    is_auto_mode: bool = False,
    is_available: bool = True,
) -> None:
    """Show notification when auto mode is unavailable.

    Equivalent to useAutoModeUnavailableNotification React hook.
    """
    if not is_auto_mode or is_available:
        return
    if add_notification:
        add_notification(
            key="auto-mode-unavailable",
            text="Auto mode is not available for this session",
            priority="high",
        )
