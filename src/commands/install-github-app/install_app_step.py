"""Install app step for GitHub app installation."""

from __future__ import annotations

from typing import Any


async def install_app_step(**_kwargs: Any) -> dict[str, str]:
    """Install the GitHub app."""
    return {"status": "installed"}
