"""Fast mode notification."""

from __future__ import annotations

from typing import Callable, Optional


def check_fast_mode(
    add_notification: Optional[Callable] = None,
    is_fast_mode: bool = False,
) -> None:
    """Show notification about fast mode status.

    Equivalent to useFastModeNotification React hook.
    """
    if not is_fast_mode or not add_notification:
        return
    add_notification(
        key="fast-mode",
        text="Fast mode enabled",
        priority="low",
    )
