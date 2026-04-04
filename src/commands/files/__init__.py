"""Files command - List all files currently in context."""

from __future__ import annotations

import os

command = {
    "type": "local",
    "name": "files",
    "description": "List all files currently in context",
    "is_enabled": lambda: os.environ.get("USER_TYPE") == "ant",
    "supports_non_interactive": True,
}
