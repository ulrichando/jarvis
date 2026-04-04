"""Chrome command implementation."""

from __future__ import annotations

from typing import Any

CHROME_EXTENSION_URL = "https://claude.ai/chrome"
CHROME_PERMISSIONS_URL = "https://clau.de/chrome/permissions"
CHROME_RECONNECT_URL = "https://clau.de/chrome/reconnect"


async def call(on_done: Any = None, context: Any = None, **_kwargs: Any) -> None:
    """Manage Claude in Chrome settings."""
    if on_done:
        on_done("Chrome extension settings menu.", {"display": "system"})
