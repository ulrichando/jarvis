"""Settings change detection and notification."""

from __future__ import annotations

from typing import Any, Callable, Optional


class SettingsChangeDetector:
    """Detects and notifies about settings file changes.

    Equivalent to useSettingsChange React hook.
    """

    def __init__(self):
        self._subscribers: list[Callable] = []

    def subscribe(self, callback: Callable) -> Callable:
        """Subscribe to settings changes. Returns unsubscribe function."""
        self._subscribers.append(callback)

        def unsubscribe():
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return unsubscribe

    def notify(self, source: str, settings: Any = None) -> None:
        """Notify all subscribers of a settings change."""
        for cb in self._subscribers:
            cb(source, settings)


# Module-level singleton
settings_change_detector = SettingsChangeDetector()


def on_settings_change(
    on_change: Callable,
    get_settings: Optional[Callable] = None,
) -> Callable:
    """Register a settings change handler. Returns unsubscribe function.

    Equivalent to useSettingsChange React hook.
    """
    def handle_change(source: str, _settings: Any = None):
        settings = get_settings() if get_settings else _settings
        on_change(source, settings)

    return settings_change_detector.subscribe(handle_change)
