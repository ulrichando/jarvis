"""Plugin validation utilities."""

from __future__ import annotations

from typing import Any


def validate_plugin(plugin_data: dict[str, Any]) -> list[str]:
    """Validate a plugin definition. Returns list of errors (empty if valid)."""
    errors: list[str] = []

    if not plugin_data.get("name"):
        errors.append("Plugin must have a name.")
    if not plugin_data.get("version"):
        errors.append("Plugin must have a version.")
    if not plugin_data.get("description"):
        errors.append("Plugin must have a description.")

    return errors
