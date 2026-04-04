"""Login command - Sign in with Anthropic account."""

from __future__ import annotations

import os

command = {
    "type": "local",
    "name": "login",
    "description": "Sign in with your Anthropic account",
    "is_enabled": lambda: not os.environ.get("DISABLE_LOGIN_COMMAND", "").lower() in ("1", "true", "yes"),
}
