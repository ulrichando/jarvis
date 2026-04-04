"""CLI print mode -- non-interactive single-query execution."""

from __future__ import annotations

import sys
from typing import Any, Optional


async def run_print_mode(
    prompt: str,
    output_format: str = "text",
    model: Optional[str] = None,
) -> None:
    """Run a single query in print mode (non-interactive)."""
    # Would invoke Brain.think() with the prompt and print result
    pass
