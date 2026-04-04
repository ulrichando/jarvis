"""Auto-compaction triggered when context window fills up."""

from __future__ import annotations

import logging
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

# Default threshold: compact when context is 80% full
DEFAULT_AUTO_COMPACT_THRESHOLD = 0.8


async def should_auto_compact(
    current_tokens: int,
    max_tokens: int,
    threshold: float = DEFAULT_AUTO_COMPACT_THRESHOLD,
) -> bool:
    """Check if auto-compaction should trigger."""
    if max_tokens <= 0:
        return False
    return (current_tokens / max_tokens) >= threshold


async def run_auto_compact(
    messages: List[Any],
    tools: Optional[List[Any]] = None,
) -> Optional[str]:
    """Run auto-compaction on the conversation."""
    from .compact import compact_conversation
    return await compact_conversation(messages, tools, direction="full")
