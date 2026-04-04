"""Plugin details helper utilities."""

from __future__ import annotations

from typing import Any


def format_plugin_details(plugin: dict[str, Any]) -> str:
    """Format plugin details for display."""
    name = plugin.get("name", "Unknown")
    description = plugin.get("description", "No description")
    version = plugin.get("version", "unknown")
    return f"{name} (v{version}): {description}"


def format_plugin_list(plugins: list[dict[str, Any]]) -> str:
    """Format a list of plugins for display."""
    if not plugins:
        return "No plugins found."
    lines = [format_plugin_details(p) for p in plugins]
    return "\n".join(lines)
