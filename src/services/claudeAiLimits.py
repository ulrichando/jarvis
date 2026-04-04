"""
Claude AI rate limit tracking and quota status management.

Tracks rate limit state from API response headers and emits
status changes to registered listeners.
"""

from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Literal, Optional, Set

logger = logging.getLogger(__name__)

QuotaStatus = Literal["allowed", "allowed_warning", "rejected"]
RateLimitType = Literal["five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet", "overage"]
OverageDisabledReason = Literal[
    "overage_not_provisioned",
    "org_level_disabled",
    "org_level_disabled_until",
    "out_of_credits",
    "seat_tier_level_disabled",
    "member_level_disabled",
    "seat_tier_zero_credit_limit",
    "group_zero_credit_limit",
    "member_zero_credit_limit",
    "org_service_level_disabled",
    "org_service_zero_credit_limit",
    "no_limits_configured",
    "unknown",
]


@dataclass
class ClaudeAILimits:
    """Current rate limit state."""
    status: QuotaStatus = "allowed"
    unified_rate_limit_fallback_available: bool = False
    resets_at: Optional[float] = None
    rate_limit_type: Optional[RateLimitType] = None
    utilization: Optional[float] = None
    overage_status: Optional[QuotaStatus] = None
    overage_resets_at: Optional[float] = None
    overage_disabled_reason: Optional[OverageDisabledReason] = None
    is_using_overage: bool = False
    surpassed_threshold: Optional[float] = None


@dataclass
class RawWindowUtilization:
    """Raw per-window utilization from response headers."""
    utilization: float = 0.0
    resets_at: float = 0.0


@dataclass
class RawUtilization:
    """Raw utilization data across windows."""
    five_hour: Optional[RawWindowUtilization] = None
    seven_day: Optional[RawWindowUtilization] = None


@dataclass
class EarlyWarningThreshold:
    utilization: float  # 0-1 scale
    time_pct: float  # 0-1 scale


@dataclass
class EarlyWarningConfig:
    rate_limit_type: RateLimitType
    claim_abbrev: str
    window_seconds: int
    thresholds: list[EarlyWarningThreshold] = field(default_factory=list)


EARLY_WARNING_CONFIGS: list[EarlyWarningConfig] = [
    EarlyWarningConfig(
        rate_limit_type="five_hour",
        claim_abbrev="5h",
        window_seconds=5 * 60 * 60,
        thresholds=[EarlyWarningThreshold(utilization=0.9, time_pct=0.72)],
    ),
    EarlyWarningConfig(
        rate_limit_type="seven_day",
        claim_abbrev="7d",
        window_seconds=7 * 24 * 60 * 60,
        thresholds=[
            EarlyWarningThreshold(utilization=0.75, time_pct=0.6),
            EarlyWarningThreshold(utilization=0.5, time_pct=0.35),
            EarlyWarningThreshold(utilization=0.25, time_pct=0.15),
        ],
    ),
]

EARLY_WARNING_CLAIM_MAP: Dict[str, RateLimitType] = {
    "5h": "five_hour",
    "7d": "seven_day",
    "overage": "overage",
}

RATE_LIMIT_DISPLAY_NAMES: Dict[str, str] = {
    "five_hour": "session limit",
    "seven_day": "weekly limit",
    "seven_day_opus": "Opus limit",
    "seven_day_sonnet": "Sonnet limit",
    "overage": "extra usage limit",
}

# Module-level state
current_limits = ClaudeAILimits()
raw_utilization = RawUtilization()
status_listeners: Set[Callable[[ClaudeAILimits], None]] = set()


def get_rate_limit_display_name(rate_limit_type: str) -> str:
    return RATE_LIMIT_DISPLAY_NAMES.get(rate_limit_type, rate_limit_type)


def get_raw_utilization() -> RawUtilization:
    return raw_utilization


def _compute_time_progress(resets_at: float, window_seconds: int) -> float:
    """Calculate what fraction of a time window has elapsed."""
    now_seconds = time.time()
    window_start = resets_at - window_seconds
    elapsed = now_seconds - window_start
    return max(0.0, min(1.0, elapsed / window_seconds))


def emit_status_change(limits: ClaudeAILimits) -> None:
    """Update current limits and notify listeners."""
    global current_limits
    current_limits = limits
    for listener in status_listeners:
        listener(limits)


def _extract_raw_utilization(headers: Dict[str, str]) -> RawUtilization:
    """Extract raw utilization from response headers."""
    result = RawUtilization()
    for key, abbrev in [("five_hour", "5h"), ("seven_day", "7d")]:
        util_val = headers.get(f"anthropic-ratelimit-unified-{abbrev}-utilization")
        reset_val = headers.get(f"anthropic-ratelimit-unified-{abbrev}-reset")
        if util_val is not None and reset_val is not None:
            window = RawWindowUtilization(
                utilization=float(util_val), resets_at=float(reset_val)
            )
            setattr(result, key, window)
    return result


def _get_header_based_early_warning(
    headers: Dict[str, str],
    fallback_available: bool,
) -> Optional[ClaudeAILimits]:
    """Check for surpassed-threshold header."""
    for claim_abbrev, rate_limit_type in EARLY_WARNING_CLAIM_MAP.items():
        threshold = headers.get(
            f"anthropic-ratelimit-unified-{claim_abbrev}-surpassed-threshold"
        )
        if threshold is not None:
            util_header = headers.get(
                f"anthropic-ratelimit-unified-{claim_abbrev}-utilization"
            )
            reset_header = headers.get(
                f"anthropic-ratelimit-unified-{claim_abbrev}-reset"
            )
            utilization = float(util_header) if util_header else None
            resets_at = float(reset_header) if reset_header else None
            return ClaudeAILimits(
                status="allowed_warning",
                resets_at=resets_at,
                rate_limit_type=rate_limit_type,
                utilization=utilization,
                unified_rate_limit_fallback_available=fallback_available,
                is_using_overage=False,
                surpassed_threshold=float(threshold),
            )
    return None


def _get_time_relative_early_warning(
    headers: Dict[str, str],
    config: EarlyWarningConfig,
    fallback_available: bool,
) -> Optional[ClaudeAILimits]:
    """Check time-relative early warning thresholds."""
    util_header = headers.get(
        f"anthropic-ratelimit-unified-{config.claim_abbrev}-utilization"
    )
    reset_header = headers.get(
        f"anthropic-ratelimit-unified-{config.claim_abbrev}-reset"
    )
    if util_header is None or reset_header is None:
        return None

    utilization = float(util_header)
    resets_at = float(reset_header)
    time_progress = _compute_time_progress(resets_at, config.window_seconds)

    should_warn = any(
        utilization >= t.utilization and time_progress <= t.time_pct
        for t in config.thresholds
    )

    if not should_warn:
        return None

    return ClaudeAILimits(
        status="allowed_warning",
        resets_at=resets_at,
        rate_limit_type=config.rate_limit_type,
        utilization=utilization,
        unified_rate_limit_fallback_available=fallback_available,
        is_using_overage=False,
    )


def _get_early_warning_from_headers(
    headers: Dict[str, str],
    fallback_available: bool,
) -> Optional[ClaudeAILimits]:
    """Get early warning using header-based detection with time-relative fallback."""
    header_warning = _get_header_based_early_warning(headers, fallback_available)
    if header_warning:
        return header_warning

    for config in EARLY_WARNING_CONFIGS:
        warning = _get_time_relative_early_warning(headers, config, fallback_available)
        if warning:
            return warning

    return None


def _compute_new_limits_from_headers(headers: Dict[str, str]) -> ClaudeAILimits:
    """Compute new limits from API response headers."""
    status = headers.get("anthropic-ratelimit-unified-status", "allowed")
    resets_at_header = headers.get("anthropic-ratelimit-unified-reset")
    resets_at = float(resets_at_header) if resets_at_header else None
    fallback_available = (
        headers.get("anthropic-ratelimit-unified-fallback") == "available"
    )

    rate_limit_type = headers.get("anthropic-ratelimit-unified-representative-claim")
    overage_status = headers.get("anthropic-ratelimit-unified-overage-status")
    overage_resets_at_header = headers.get("anthropic-ratelimit-unified-overage-reset")
    overage_resets_at = float(overage_resets_at_header) if overage_resets_at_header else None
    overage_disabled_reason = headers.get(
        "anthropic-ratelimit-unified-overage-disabled-reason"
    )

    is_using_overage = (
        status == "rejected"
        and overage_status in ("allowed", "allowed_warning")
    )

    final_status = status
    if status in ("allowed", "allowed_warning"):
        early_warning = _get_early_warning_from_headers(headers, fallback_available)
        if early_warning:
            return early_warning
        final_status = "allowed"

    return ClaudeAILimits(
        status=final_status,
        resets_at=resets_at,
        unified_rate_limit_fallback_available=fallback_available,
        rate_limit_type=rate_limit_type,
        overage_status=overage_status,
        overage_resets_at=overage_resets_at,
        overage_disabled_reason=overage_disabled_reason,
        is_using_overage=is_using_overage,
    )


def extract_quota_status_from_headers(headers: Dict[str, str]) -> None:
    """Extract and update quota status from API response headers."""
    global raw_utilization
    raw_utilization = _extract_raw_utilization(headers)
    new_limits = _compute_new_limits_from_headers(headers)

    if current_limits != new_limits:
        emit_status_change(new_limits)


def extract_quota_status_from_error(error: Any) -> None:
    """Extract quota status from an API error response."""
    status_code = getattr(error, "status", getattr(error, "status_code", None))
    if status_code != 429:
        return

    try:
        headers = getattr(error, "headers", {})
        if isinstance(headers, dict):
            global raw_utilization
            raw_utilization = _extract_raw_utilization(headers)
            new_limits = _compute_new_limits_from_headers(headers)
        else:
            new_limits = ClaudeAILimits(**vars(current_limits))

        new_limits.status = "rejected"

        if current_limits != new_limits:
            emit_status_change(new_limits)
    except Exception as e:
        logger.error(f"Error extracting quota status: {e}")


async def check_quota_status() -> None:
    """Check current quota status via a minimal API request."""
    # Placeholder - would make a real API call in production
    pass
