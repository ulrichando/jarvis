"""Success step for GitHub app installation."""

from __future__ import annotations

from typing import Any


async def success_step(**_kwargs: Any) -> dict[str, str]:
    """Show the success message."""
    return {"status": "success", "message": "GitHub app installed successfully."}
