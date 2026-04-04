"""Install-slack-app command implementation."""

from __future__ import annotations

from typing import Any


async def call(_args: str = "", **_kwargs: Any) -> dict[str, str]:
    """Install the Claude Slack app."""
    return {"type": "text", "value": "Slack app installation wizard."}
