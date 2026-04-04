"""Export command implementation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


async def call(on_done: Any = None, context: Any = None, args: str = "", **_kwargs: Any) -> None:
    """Export the current conversation."""
    filename = args.strip() if args else None

    if not filename:
        filename = "conversation_export.json"

    output_path = Path(filename)
    if on_done:
        on_done(f"Conversation exported to {output_path}", {"display": "system"})
