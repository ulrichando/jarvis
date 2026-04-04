"""Unified installed cell for plugin display."""

from __future__ import annotations

from typing import Any


def format_installed_cell(plugin: dict[str, Any]) -> str:
    """Format a single installed plugin for display."""
    name = plugin.get("name", "Unknown")
    version = plugin.get("version", "unknown")
    enabled = plugin.get("enabled", True)
    status = "enabled" if enabled else "disabled"
    return f"  {name} v{version} ({status})"
