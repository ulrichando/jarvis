"""
Conversation compaction service.

Summarizes conversation history to reduce token usage while
preserving essential context for ongoing work.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .prompt import build_compact_prompt

logger = logging.getLogger(__name__)


async def compact_conversation(
    messages: List[Any],
    tools: Optional[List[Any]] = None,
    direction: str = "full",
) -> Optional[str]:
    """Compact a conversation by generating a summary.

    Args:
        messages: Conversation messages to compact
        tools: Available tools (for context)
        direction: 'full' or 'partial'

    Returns:
        Summary text, or None on failure
    """
    if not messages:
        return None

    prompt = build_compact_prompt(direction)
    # In a full implementation, this would call the LLM
    logger.debug(f"[compact] Would compact {len(messages)} messages ({direction})")
    return None


def format_compact_summary(raw_summary: str) -> str:
    """Extract and format the summary from a compaction response.

    Strips <analysis> blocks and extracts <summary> content.
    """
    import re

    # Remove analysis blocks
    cleaned = re.sub(r"<analysis>.*?</analysis>", "", raw_summary, flags=re.DOTALL)

    # Extract summary content
    match = re.search(r"<summary>(.*?)</summary>", cleaned, re.DOTALL)
    if match:
        return match.group(1).strip()

    return cleaned.strip()
