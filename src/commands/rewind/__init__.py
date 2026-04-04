"""Rewind command - Restore code and/or conversation to a previous point."""

from __future__ import annotations

command = {
    "type": "local",
    "name": "rewind",
    "description": "Restore the code and/or conversation to a previous point",
    "aliases": ["checkpoint"],
    "supports_non_interactive": False,
}
