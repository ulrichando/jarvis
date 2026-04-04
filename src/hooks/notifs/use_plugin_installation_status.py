"""Plugin installation status notification."""

from __future__ import annotations

from typing import Callable, Optional


def check_plugin_installation(
    add_notification: Optional[Callable] = None,
    installing_plugins: Optional[list] = None,
    failed_plugins: Optional[list] = None,
) -> None:
    """Show plugin installation status.

    Equivalent to usePluginInstallationStatus React hook.
    """
    if not add_notification:
        return
    if installing_plugins:
        add_notification(
            key="plugin-install",
            text=f"Installing {len(installing_plugins)} plugin(s)...",
            priority="low",
        )
    if failed_plugins:
        names = ", ".join(failed_plugins)
        add_notification(
            key="plugin-install-failed",
            text=f"Failed to install: {names}",
            priority="high",
        )
