"""
Thinking mode configuration and utilities.

Provides configuration types and helper functions for extended thinking
(adaptive, enabled with budget, or disabled).
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Union


@dataclass
class ThinkingConfigAdaptive:
    type: Literal["adaptive"] = "adaptive"


@dataclass
class ThinkingConfigEnabled:
    type: Literal["enabled"] = "enabled"
    budget_tokens: int = 0


@dataclass
class ThinkingConfigDisabled:
    type: Literal["disabled"] = "disabled"


ThinkingConfig = Union[ThinkingConfigAdaptive, ThinkingConfigEnabled, ThinkingConfigDisabled]


RAINBOW_COLORS = [
    "rainbow_red",
    "rainbow_orange",
    "rainbow_yellow",
    "rainbow_green",
    "rainbow_blue",
    "rainbow_indigo",
    "rainbow_violet",
]

RAINBOW_SHIMMER_COLORS = [
    "rainbow_red_shimmer",
    "rainbow_orange_shimmer",
    "rainbow_yellow_shimmer",
    "rainbow_green_shimmer",
    "rainbow_blue_shimmer",
    "rainbow_indigo_shimmer",
    "rainbow_violet_shimmer",
]


def has_ultrathink_keyword(text: str) -> bool:
    """Check if text contains the 'ultrathink' keyword."""
    return bool(re.search(r"\bultrathink\b", text, re.IGNORECASE))


def find_thinking_trigger_positions(text: str) -> List[Dict[str, Any]]:
    """
    Find positions of 'ultrathink' keyword in text (for UI highlighting/notification).
    """
    positions = []
    for match in re.finditer(r"\bultrathink\b", text, re.IGNORECASE):
        positions.append({
            "word": match.group(0),
            "start": match.start(),
            "end": match.end(),
        })
    return positions


def get_rainbow_color(char_index: int, shimmer: bool = False) -> str:
    """Get a rainbow color for a character index."""
    colors = RAINBOW_SHIMMER_COLORS if shimmer else RAINBOW_COLORS
    return colors[char_index % len(colors)]


def model_supports_thinking(model: str) -> bool:
    """Check if a model supports extended thinking."""
    canonical = model.lower()
    # Claude 4+ models support thinking (not Claude 3.x)
    if "claude-3-" in canonical:
        return False
    return "sonnet-4" in canonical or "opus-4" in canonical


def model_supports_adaptive_thinking(model: str) -> bool:
    """Check if a model supports adaptive thinking."""
    canonical = model.lower()
    if "opus-4-6" in canonical or "sonnet-4-6" in canonical:
        return True
    if "opus" in canonical or "sonnet" in canonical or "haiku" in canonical:
        return False
    # Default to True for unknown models
    return True


def should_enable_thinking_by_default() -> bool:
    """Check whether thinking should be enabled by default."""
    import os

    max_thinking = os.environ.get("MAX_THINKING_TOKENS")
    if max_thinking:
        try:
            return int(max_thinking) > 0
        except ValueError:
            pass

    # Enable thinking by default unless explicitly disabled
    return True
