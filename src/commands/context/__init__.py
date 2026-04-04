"""Context command - Visualize current context usage."""

from __future__ import annotations

command = {
    "type": "local",
    "name": "context",
    "description": "Visualize current context usage as a colored grid",
}

context_non_interactive = {
    "type": "local",
    "name": "context",
    "supports_non_interactive": True,
    "description": "Show current context usage",
}
