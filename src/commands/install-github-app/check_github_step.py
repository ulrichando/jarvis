"""Check GitHub step for GitHub app installation."""

from __future__ import annotations

from typing import Any


async def check_github_step(**_kwargs: Any) -> dict[str, Any]:
    """Check GitHub authentication and repository access."""
    return {"authenticated": False, "repos": []}
