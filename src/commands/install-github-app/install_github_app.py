"""Install-github-app command implementation."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, **_kwargs: Any) -> None:
    """Set up Claude GitHub Actions for a repository."""
    if on_done:
        on_done("GitHub app installation wizard.", {"display": "system"})
