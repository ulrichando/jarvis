"""Clear command - Clear conversation history and free up context."""

from __future__ import annotations

command = {
    "type": "local",
    "name": "clear",
    "description": "Clear conversation history and free up context",
    "aliases": ["reset", "new"],
    "supports_non_interactive": False,
}
