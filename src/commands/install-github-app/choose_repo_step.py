"""Choose repo step for GitHub app installation."""

from __future__ import annotations

from typing import Any, Optional


async def choose_repo_step(repos: list[str] | None = None, **_kwargs: Any) -> Optional[str]:
    """Let user choose a repository."""
    if not repos:
        return None
    return repos[0] if repos else None
