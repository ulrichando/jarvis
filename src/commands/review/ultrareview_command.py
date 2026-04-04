"""Ultrareview command implementation."""

from __future__ import annotations

from typing import Any

CCR_TERMS_URL = "https://code.claude.com/docs/en/claude-code-on-the-web"


async def call(on_done: Any = None, context: Any = None, args: str = "", **_kwargs: Any) -> None:
    """Run ultra review (remote bughunter)."""
    if on_done:
        on_done(f"Ultrareview: Finds and verifies bugs in your branch. See {CCR_TERMS_URL}", {"display": "system"})
