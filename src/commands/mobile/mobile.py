"""Mobile command implementation."""

from __future__ import annotations

from typing import Any


async def call(on_done: Any = None, context: Any = None, **_kwargs: Any) -> None:
    """Show QR code to download the Claude mobile app."""
    if on_done:
        on_done("Mobile app download QR code.", {"display": "system"})
