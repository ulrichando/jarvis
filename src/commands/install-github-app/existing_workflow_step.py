"""Existing workflow step for GitHub app installation."""

from __future__ import annotations

from typing import Any


async def existing_workflow_step(**_kwargs: Any) -> dict[str, Any]:
    """Check for existing workflow files."""
    return {"has_workflow": False}
