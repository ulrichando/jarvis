"""Settings validation error notifications."""

from __future__ import annotations

from typing import Callable, List, Optional


def check_settings_errors(
    add_notification: Optional[Callable] = None,
    errors: Optional[List[str]] = None,
) -> None:
    """Show notifications for settings validation errors.

    Equivalent to useSettingsErrors React hook.
    """
    if not errors or not add_notification:
        return
    for i, error in enumerate(errors):
        add_notification(
            key=f"settings-error-{i}",
            text=f"Settings error: {error}",
            priority="high",
        )
