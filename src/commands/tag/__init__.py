"""Tag command - Toggle a searchable tag on the current session."""

from __future__ import annotations

import os

command = {
    "type": "local",
    "name": "tag",
    "description": "Toggle a searchable tag on the current session",
    "is_enabled": lambda: os.environ.get("USER_TYPE") == "ant",
    "argument_hint": "<tag-name>",
}
