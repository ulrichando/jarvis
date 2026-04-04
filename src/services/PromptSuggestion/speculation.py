"""Speculative prompt execution for faster responses."""

from __future__ import annotations

import os
from typing import Any, Optional


def is_speculation_enabled() -> bool:
    """Check if speculative execution is enabled."""
    return os.environ.get("CLAUDE_CODE_ENABLE_SPECULATION", "").lower() in ("1", "true")


async def start_speculation(messages: list, tools: list) -> Optional[Any]:
    """Start speculative execution on the most likely next prompt."""
    if not is_speculation_enabled():
        return None
    # Placeholder
    return None
