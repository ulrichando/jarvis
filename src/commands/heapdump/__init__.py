"""Heapdump command - Dump the heap for debugging."""

from __future__ import annotations

command = {
    "type": "local",
    "name": "heapdump",
    "description": "Dump the heap to ~/Desktop",
    "is_hidden": True,
    "supports_non_interactive": True,
}
