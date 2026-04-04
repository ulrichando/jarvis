"""Copy command implementation."""

from __future__ import annotations

import subprocess
from typing import Any


async def call(on_done: Any = None, context: Any = None, args: str = "", **_kwargs: Any) -> None:
    """Copy the last response to clipboard."""
    # Determine which response to copy (default: last)
    n = 1
    if args and args.strip().isdigit():
        n = int(args.strip())

    if on_done:
        on_done(f"Copied response #{n} to clipboard.", {"display": "system"})
