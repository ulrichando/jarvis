"""Time-based micro-compaction configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class TimeBasedMCConfig:
    """Configuration for time-based micro-compaction.

    Controls how aggressively tool results are truncated
    based on their age in the conversation.
    """
    recent_window_turns: int = 5
    recent_max_tokens: int = 8000
    old_max_tokens: int = 2000
    very_old_max_tokens: int = 500
    very_old_threshold_turns: int = 20


DEFAULT_CONFIG = TimeBasedMCConfig()


def get_max_tokens_for_age(turns_ago: int, config: Optional[TimeBasedMCConfig] = None) -> int:
    """Get the max token budget for a tool result based on its age."""
    cfg = config or DEFAULT_CONFIG
    if turns_ago <= cfg.recent_window_turns:
        return cfg.recent_max_tokens
    if turns_ago >= cfg.very_old_threshold_turns:
        return cfg.very_old_max_tokens
    return cfg.old_max_tokens
