"""Plugin auto-update notification."""

from __future__ import annotations

from typing import Callable, Optional


def check_plugin_autoupdate(
    add_notification: Optional[Callable] = None,
    updated_plugins: Optional[list] = None,
) -> None:
    """Show notification about auto-updated plugins.

    Equivalent to usePluginAutoupdateNotification React hook.
    """
    if not updated_plugins or not add_notification:
        return
    count = len(updated_plugins)
    text = f"{count} plugin{'s' if count != 1 else ''} auto-updated"
    add_notification(key="plugin-autoupdate", text=text, priority="low")
