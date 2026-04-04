"""
Effort level management for controlling LLM thinking depth.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Optional, Union

EffortLevel = Literal["low", "medium", "high", "max"]
EffortValue = Union[EffortLevel, int]

EFFORT_LEVELS: tuple[EffortLevel, ...] = ("low", "medium", "high", "max")


def is_effort_level(value: str) -> bool:
    """Check if a string is a valid effort level."""
    return value in EFFORT_LEVELS


def parse_effort_value(value: object) -> Optional[EffortValue]:
    """Parse a raw value into an EffortValue."""
    if value is None or value == "":
        return None

    if isinstance(value, int) and is_valid_numeric_effort(value):
        return value

    s = str(value).lower()
    if is_effort_level(s):
        return s  # type: ignore[return-value]

    try:
        numeric = int(s)
        if is_valid_numeric_effort(numeric):
            return numeric
    except ValueError:
        pass

    return None


def to_persistable_effort(value: Optional[EffortValue]) -> Optional[EffortLevel]:
    """
    Filter effort values to only those safe to persist.
    Numeric values are model-default only and not persisted.
    'max' is session-scoped for external users.
    """
    if value in ("low", "medium", "high"):
        return value  # type: ignore[return-value]
    if value == "max" and os.environ.get("USER_TYPE") == "ant":
        return "max"
    return None


def is_valid_numeric_effort(value: int) -> bool:
    """Check if a numeric effort value is valid."""
    return isinstance(value, int)


def convert_effort_value_to_level(value: EffortValue) -> EffortLevel:
    """Convert an EffortValue (string or numeric) to an EffortLevel string."""
    if isinstance(value, str):
        return value if is_effort_level(value) else "high"

    if os.environ.get("USER_TYPE") == "ant" and isinstance(value, int):
        if value <= 50:
            return "low"
        if value <= 85:
            return "medium"
        if value <= 100:
            return "high"
        return "max"

    return "high"


def get_effort_level_description(level: EffortLevel) -> str:
    """Get user-facing description for effort levels."""
    descriptions: dict[EffortLevel, str] = {
        "low": "Quick, straightforward implementation with minimal overhead",
        "medium": "Balanced approach with standard implementation and testing",
        "high": "Comprehensive implementation with extensive testing and documentation",
        "max": "Maximum capability with deepest reasoning (Opus 4.6 only)",
    }
    return descriptions.get(level, descriptions["medium"])


def get_effort_value_description(value: EffortValue) -> str:
    """Get user-facing description for effort values (both string and numeric)."""
    if os.environ.get("USER_TYPE") == "ant" and isinstance(value, int):
        return f"[ANT-ONLY] Numeric effort value of {value}"

    if isinstance(value, str):
        return get_effort_level_description(value)

    return "Balanced approach with standard implementation and testing"


def get_effort_suffix(model: str, effort_value: Optional[EffortValue]) -> str:
    """
    Build the ' with {level} effort' suffix shown in display.
    Returns empty string if no explicit effort value is set.
    """
    if effort_value is None:
        return ""
    level = convert_effort_value_to_level(effort_value)
    return f" with {level} effort"


def resolve_picker_effort_persistence(
    picked: Optional[EffortLevel],
    model_default: EffortLevel,
    prior_persisted: Optional[EffortLevel],
    toggled_in_picker: bool,
) -> Optional[EffortLevel]:
    """
    Decide what effort level to persist when the user selects a model.
    Keeps explicit prior choices sticky while letting defaults fall through.
    """
    had_explicit = prior_persisted is not None or toggled_in_picker
    return picked if (had_explicit or picked != model_default) else None


def get_effort_env_override() -> Optional[EffortValue]:
    """
    Get effort override from environment variable.
    Returns None if not set, or the parsed value.
    Special: 'unset'/'auto' returns a sentinel (represented as None with a
    distinction handled by callers).
    """
    env_override = os.environ.get("CLAUDE_CODE_EFFORT_LEVEL")
    if env_override is None:
        return None
    if env_override.lower() in ("unset", "auto"):
        return None
    return parse_effort_value(env_override)
