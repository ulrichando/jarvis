"""Desktop command - Launch JARVIS desktop overlay."""

from __future__ import annotations

command = {
    "type": "local",
    "name": "desktop",
    "aliases": ["app"],
    "description": "Launch JARVIS desktop overlay (GTK+WebKit)",
    "is_enabled": lambda: True,
}
