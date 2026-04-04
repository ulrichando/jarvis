"""Logout command - Sign out from Anthropic account."""

from __future__ import annotations

import os

command = {
    "type": "local",
    "name": "logout",
    "description": "Sign out from your Anthropic account",
    "is_enabled": lambda: not os.environ.get("DISABLE_LOGOUT_COMMAND", "").lower() in ("1", "true", "yes"),
}
