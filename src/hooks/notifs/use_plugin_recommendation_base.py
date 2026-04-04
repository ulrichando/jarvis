"""Base plugin recommendation notification."""

from __future__ import annotations

from typing import Callable, Optional


class PluginRecommendationBase:
    """Base class for plugin recommendation notifications.

    Equivalent to usePluginRecommendationBase React hook.
    """

    def __init__(
        self,
        add_notification: Optional[Callable] = None,
        plugin_name: str = "",
        reason: str = "",
    ):
        self._add_notification = add_notification
        self._plugin_name = plugin_name
        self._reason = reason
        self._shown = False

    def check(self) -> None:
        if self._shown or not self._add_notification:
            return
        self._shown = True
        self._add_notification(
            key=f"plugin-rec-{self._plugin_name}",
            text=f"Recommended plugin: {self._plugin_name} - {self._reason}",
            priority="low",
        )

    def dismiss(self) -> None:
        self._shown = True
