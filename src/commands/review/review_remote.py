"""Review remote utilities."""

from __future__ import annotations

from typing import Any


async def review_remote_pr(pr_number: int, **_kwargs: Any) -> dict[str, str]:
    """Review a remote pull request."""
    return {"type": "text", "value": f"Remote review for PR #{pr_number}."}
