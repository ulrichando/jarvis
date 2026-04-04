"""Prompt suggestion service for suggesting next user prompts."""

from __future__ import annotations

import logging
import os
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

PromptVariant = str  # 'user_intent' | 'stated_intent'


def get_prompt_variant() -> PromptVariant:
    return "user_intent"


def should_enable_prompt_suggestion() -> bool:
    """Check if prompt suggestions should be enabled."""
    env_val = os.environ.get("CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION")
    if env_val is not None:
        return env_val.lower() in ("1", "true", "yes")
    return False


async def generate_prompt_suggestions(
    messages: List[Any],
    max_suggestions: int = 3,
) -> List[str]:
    """Generate prompt suggestions based on conversation context."""
    if not should_enable_prompt_suggestion():
        return []
    # Placeholder - would call LLM for suggestions
    return []
