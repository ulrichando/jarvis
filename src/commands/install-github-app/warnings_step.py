"""Warnings step for GitHub app installation."""

from __future__ import annotations

from typing import Any


async def warnings_step(**_kwargs: Any) -> list[str]:
    """Show any warnings before proceeding."""
    return []
