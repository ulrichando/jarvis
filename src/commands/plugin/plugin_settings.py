"""Plugin settings management."""

from __future__ import annotations

from typing import Any


def get_plugin_settings(plugin_name: str) -> dict[str, Any]:
    """Get settings for a specific plugin."""
    return {}


def save_plugin_settings(plugin_name: str, settings: dict[str, Any]) -> None:
    """Save settings for a specific plugin."""
    pass
