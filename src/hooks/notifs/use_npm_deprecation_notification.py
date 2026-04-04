"""NPM deprecation notification."""

from __future__ import annotations

from typing import Callable, Optional


def check_npm_deprecation(
    add_notification: Optional[Callable] = None,
    is_npm_install: bool = False,
) -> None:
    """Show notification about NPM installation deprecation.

    Equivalent to useNpmDeprecationNotification React hook.
    """
    if not is_npm_install or not add_notification:
        return
    add_notification(
        key="npm-deprecation",
        text="NPM installation is deprecated. Use the official installer instead.",
        priority="high",
    )
