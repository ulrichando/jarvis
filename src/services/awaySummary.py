"""
Away summary generation.

Generates a short session recap for "while you were away" cards.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

RECENT_MESSAGE_WINDOW = 30


def _build_away_summary_prompt(memory: Optional[str]) -> str:
    memory_block = f"Session memory (broader context):\n{memory}\n\n" if memory else ""
    return (
        f"{memory_block}The user stepped away and is coming back. "
        "Write exactly 1-3 short sentences. Start by stating the high-level task "
        "-- what they are building or debugging, not implementation details. "
        "Next: the concrete next step. Skip status reports and commit recaps."
    )


async def generate_away_summary(
    messages: List[Any],
    signal: Optional[Any] = None,
) -> Optional[str]:
    """Generate a short session recap for the 'while you were away' card.

    Returns None on abort, empty transcript, or error.
    """
    if not messages:
        return None

    try:
        # Get session memory content if available
        memory: Optional[str] = None
        try:
            from .SessionMemory.sessionMemoryUtils import get_session_memory_content
            memory = await get_session_memory_content()
        except ImportError:
            pass

        recent = list(messages[-RECENT_MESSAGE_WINDOW:])
        prompt = _build_away_summary_prompt(memory)

        # In a real implementation, this would call the LLM
        # For now, return None as placeholder
        return None
    except Exception as e:
        logger.debug(f"[awaySummary] generation failed: {e}")
        return None
