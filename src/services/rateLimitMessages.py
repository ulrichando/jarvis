"""
Centralized rate limit message generation.

Single source of truth for all rate limit-related messages.
"""

from __future__ import annotations

import os
from typing import Optional

from .claudeAiLimits import ClaudeAILimits


RATE_LIMIT_ERROR_PREFIXES = (
    "You've hit your",
    "You've used",
    "You're now using extra usage",
    "You're close to",
    "You're out of extra usage",
)


def is_rate_limit_error_message(text: str) -> bool:
    """Check if a message is a rate limit error."""
    return any(text.startswith(prefix) for prefix in RATE_LIMIT_ERROR_PREFIXES)


class RateLimitMessage:
    def __init__(self, message: str, severity: str):
        self.message = message
        self.severity = severity  # 'error' | 'warning'


def get_rate_limit_message(
    limits: ClaudeAILimits, model: str
) -> Optional[RateLimitMessage]:
    """Get the appropriate rate limit message based on limit state.

    Returns None if no message should be shown.
    """
    if limits.is_using_overage:
        if limits.overage_status == "allowed_warning":
            return RateLimitMessage(
                "You're close to your extra usage spending limit", "warning"
            )
        return None

    if limits.status == "rejected":
        return RateLimitMessage(_get_limit_reached_text(limits, model), "error")

    if limits.status == "allowed_warning":
        WARNING_THRESHOLD = 0.7
        if limits.utilization is not None and limits.utilization < WARNING_THRESHOLD:
            return None

        text = _get_early_warning_text(limits)
        if text:
            return RateLimitMessage(text, "warning")

    return None


def get_rate_limit_error_message(
    limits: ClaudeAILimits, model: str
) -> Optional[str]:
    """Get error message for API errors."""
    message = get_rate_limit_message(limits, model)
    if message and message.severity == "error":
        return message.message
    return None


def get_rate_limit_warning(
    limits: ClaudeAILimits, model: str
) -> Optional[str]:
    """Get warning message for UI footer."""
    message = get_rate_limit_message(limits, model)
    if message and message.severity == "warning":
        return message.message
    return None


def _format_reset_time(resets_at: float, short: bool = True) -> str:
    """Format a reset timestamp as human-readable string."""
    import time
    remaining = resets_at - time.time()
    if remaining <= 0:
        return "now"
    hours = remaining / 3600
    if hours < 1:
        minutes = int(remaining / 60)
        return f"in {minutes}m"
    if hours < 24:
        return f"in {int(hours)}h"
    days = int(hours / 24)
    return f"in {days}d"


def _get_limit_reached_text(limits: ClaudeAILimits, model: str) -> str:
    """Generate text for when a limit is reached."""
    resets_at = limits.resets_at
    reset_time = _format_reset_time(resets_at) if resets_at else None
    reset_message = f" - resets {reset_time}" if reset_time else ""

    if limits.overage_status == "rejected":
        if limits.overage_disabled_reason == "out_of_credits":
            return f"You're out of extra usage{reset_message}"
        return _format_limit_reached("limit", reset_message, model)

    type_map = {
        "seven_day_sonnet": "Sonnet limit",
        "seven_day_opus": "Opus limit",
        "seven_day": "weekly limit",
        "five_hour": "session limit",
    }
    limit_name = type_map.get(limits.rate_limit_type or "", "usage limit")
    return _format_limit_reached(limit_name, reset_message, model)


def _get_early_warning_text(limits: ClaudeAILimits) -> Optional[str]:
    """Generate early warning text."""
    limit_names = {
        "seven_day": "weekly limit",
        "five_hour": "session limit",
        "seven_day_opus": "Opus limit",
        "seven_day_sonnet": "Sonnet limit",
        "overage": "extra usage",
    }

    limit_name = limit_names.get(limits.rate_limit_type or "")
    if limit_name is None:
        return None

    used = int(limits.utilization * 100) if limits.utilization else None
    reset_time = _format_reset_time(limits.resets_at) if limits.resets_at else None

    if used and reset_time:
        return f"You've used {used}% of your {limit_name} - resets {reset_time}"
    if used:
        return f"You've used {used}% of your {limit_name}"
    if reset_time:
        return f"Approaching {limit_name} - resets {reset_time}"
    return f"Approaching {limit_name}"


def _format_limit_reached(limit: str, reset_message: str, model: str) -> str:
    """Format the limit reached text."""
    return f"You've hit your {limit}{reset_message}"


def get_using_overage_text(limits: ClaudeAILimits) -> str:
    """Get notification text for overage mode transitions."""
    reset_time = _format_reset_time(limits.resets_at) if limits.resets_at else ""

    limit_names = {
        "five_hour": "session limit",
        "seven_day": "weekly limit",
        "seven_day_opus": "Opus limit",
        "seven_day_sonnet": "Sonnet limit",
    }
    limit_name = limit_names.get(limits.rate_limit_type or "", "")

    if not limit_name:
        return "Now using extra usage"

    reset_msg = f" - Your {limit_name} resets {reset_time}" if reset_time else ""
    return f"You're now using extra usage{reset_msg}"
