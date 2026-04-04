"""Desktop command - Continue session in Claude Desktop."""

from __future__ import annotations

import platform

command = {
    "type": "local",
    "name": "desktop",
    "aliases": ["app"],
    "description": "Continue the current session in Claude Desktop",
    "availability": ["claude-ai"],
    "is_enabled": lambda: platform.system() in ("Darwin", "Windows"),
}
