"""Deprecation warning notification."""

from __future__ import annotations

from typing import Callable, Optional


def check_deprecation_warning(
    add_notification: Optional[Callable] = None,
    deprecated_features: Optional[list] = None,
) -> None:
    """Show deprecation warning notifications.

    Equivalent to useDeprecationWarningNotification React hook.
    """
    if not deprecated_features or not add_notification:
        return
    for feature in deprecated_features:
        add_notification(
            key=f"deprecation-{feature}",
            text=f"{feature} is deprecated and will be removed in a future version",
            priority="low",
        )
