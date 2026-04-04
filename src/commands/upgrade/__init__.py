"""Upgrade command - Upgrade to Max for higher rate limits."""

from __future__ import annotations

import os

command = {
    "type": "local",
    "name": "upgrade",
    "description": "Upgrade to Max for higher rate limits and more Opus",
    "availability": ["claude-ai"],
    "is_enabled": lambda: not os.environ.get("DISABLE_UPGRADE_COMMAND", "").lower() in ("1", "true", "yes"),
}
