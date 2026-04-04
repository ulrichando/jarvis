"""Parse arguments for plugin commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class PluginArgs:
    """Parsed plugin command arguments."""
    action: Optional[str] = None
    plugin_name: Optional[str] = None
    source: Optional[str] = None


def parse_plugin_args(args: str) -> PluginArgs:
    """Parse plugin command arguments.

    Examples:
        'install my-plugin' -> PluginArgs(action='install', plugin_name='my-plugin')
        'list' -> PluginArgs(action='list')
        'remove my-plugin' -> PluginArgs(action='remove', plugin_name='my-plugin')
    """
    parts = args.strip().split(maxsplit=1) if args else []

    if not parts:
        return PluginArgs()

    action = parts[0].lower()
    plugin_name = None
    source = None

    if len(parts) > 1:
        name_part = parts[1]
        if "@" in name_part:
            name_part, source = name_part.rsplit("@", 1)
        plugin_name = name_part.strip()

    return PluginArgs(action=action, plugin_name=plugin_name, source=source)
