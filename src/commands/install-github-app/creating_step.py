"""Creating step for GitHub app installation."""

from __future__ import annotations

from typing import Any


async def creating_step(**_kwargs: Any) -> dict[str, str]:
    """Show the creating progress."""
    return {"status": "creating"}
