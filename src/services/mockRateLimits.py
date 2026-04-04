"""
Mock rate limits for testing.

Allows testing various rate limit scenarios without hitting actual limits.
WARNING: For internal testing/demo purposes only.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

MockHeaderKey = Literal[
    "status", "reset", "claim", "overage-status", "overage-reset",
    "overage-disabled-reason", "fallback", "fallback-percentage",
    "retry-after", "5h-utilization", "5h-reset", "5h-surpassed-threshold",
    "7d-utilization", "7d-reset", "7d-surpassed-threshold",
]

MockScenario = Literal[
    "normal", "session-limit-reached", "approaching-weekly-limit",
    "weekly-limit-reached", "overage-active", "overage-warning",
    "overage-exhausted", "out-of-credits", "org-zero-credit-limit",
    "org-spend-cap-hit", "member-zero-credit-limit",
    "seat-tier-zero-credit-limit", "opus-limit", "opus-warning",
    "sonnet-limit", "sonnet-warning", "fast-mode-limit",
    "fast-mode-short-limit", "extra-usage-required", "clear",
]


@dataclass
class ExceededLimit:
    type: str  # five_hour | seven_day | seven_day_opus | seven_day_sonnet
    resets_at: float  # Unix timestamp


# Module-level state
_mock_headers: Dict[str, str] = {}
_mock_enabled: bool = False
_mock_headerless_429_message: Optional[str] = None
_mock_subscription_type: Optional[str] = None
_mock_fast_mode_rate_limit_duration_ms: Optional[int] = None
_mock_fast_mode_rate_limit_expires_at: Optional[float] = None
_exceeded_limits: List[ExceededLimit] = []
DEFAULT_MOCK_SUBSCRIPTION = "max"


def _now_epoch() -> float:
    return time.time()


def _update_retry_after() -> None:
    """Update retry-after based on current state."""
    status = _mock_headers.get("anthropic-ratelimit-unified-status")
    overage_status = _mock_headers.get("anthropic-ratelimit-unified-overage-status")
    reset = _mock_headers.get("anthropic-ratelimit-unified-reset")

    if status == "rejected" and (not overage_status or overage_status == "rejected") and reset:
        seconds_until_reset = max(0, int(float(reset) - _now_epoch()))
        _mock_headers["retry-after"] = str(seconds_until_reset)
    else:
        _mock_headers.pop("retry-after", None)


def _update_representative_claim() -> None:
    """Update the representative claim based on exceeded limits."""
    if not _exceeded_limits:
        _mock_headers.pop("anthropic-ratelimit-unified-representative-claim", None)
        _mock_headers.pop("anthropic-ratelimit-unified-reset", None)
        _mock_headers.pop("retry-after", None)
        return

    furthest = max(_exceeded_limits, key=lambda l: l.resets_at)
    _mock_headers["anthropic-ratelimit-unified-representative-claim"] = furthest.type
    _mock_headers["anthropic-ratelimit-unified-reset"] = str(int(furthest.resets_at))
    _update_retry_after()


def set_mock_header(key: MockHeaderKey, value: Optional[str]) -> None:
    """Toggle an individual mock header."""
    global _mock_enabled, _exceeded_limits

    if os.environ.get("USER_TYPE") != "ant":
        return

    _mock_enabled = True

    full_key = "retry-after" if key == "retry-after" else f"anthropic-ratelimit-unified-{key}"

    if value is None or value == "clear":
        _mock_headers.pop(full_key, None)
        if key == "claim":
            _exceeded_limits = []
        if key in ("status", "overage-status"):
            _update_retry_after()
        return

    if key in ("reset", "overage-reset"):
        try:
            hours = float(value)
            value = str(int(_now_epoch() + hours * 3600))
        except ValueError:
            pass

    if key == "claim":
        valid_claims = ["five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet"]
        if value in valid_claims:
            if value == "five_hour":
                resets_at = _now_epoch() + 5 * 3600
            elif value in ("seven_day", "seven_day_opus", "seven_day_sonnet"):
                resets_at = _now_epoch() + 7 * 24 * 3600
            else:
                resets_at = _now_epoch() + 3600

            _exceeded_limits = [l for l in _exceeded_limits if l.type != value]
            _exceeded_limits.append(ExceededLimit(type=value, resets_at=resets_at))
            _update_representative_claim()
            return

    _mock_headers[full_key] = value
    if key in ("status", "overage-status"):
        _update_retry_after()

    if not _mock_headers:
        _mock_enabled = False


def set_mock_rate_limit_scenario(scenario: MockScenario) -> None:
    """Set a complete mock rate limit scenario."""
    global _mock_enabled, _mock_headers, _mock_headerless_429_message, _exceeded_limits

    if os.environ.get("USER_TYPE") != "ant":
        return

    if scenario == "clear":
        _mock_headers = {}
        _mock_headerless_429_message = None
        _mock_enabled = False
        _exceeded_limits = []
        return

    _mock_enabled = True
    five_hours = int(_now_epoch()) + 5 * 3600
    seven_days = int(_now_epoch()) + 7 * 24 * 3600

    _mock_headers = {}
    _mock_headerless_429_message = None

    preserve = scenario in ("overage-active", "overage-warning", "overage-exhausted")
    if not preserve:
        _exceeded_limits = []

    if scenario == "normal":
        _mock_headers = {
            "anthropic-ratelimit-unified-status": "allowed",
            "anthropic-ratelimit-unified-reset": str(five_hours),
        }
    elif scenario == "session-limit-reached":
        _exceeded_limits = [ExceededLimit(type="five_hour", resets_at=five_hours)]
        _update_representative_claim()
        _mock_headers["anthropic-ratelimit-unified-status"] = "rejected"
    elif scenario == "weekly-limit-reached":
        _exceeded_limits = [ExceededLimit(type="seven_day", resets_at=seven_days)]
        _update_representative_claim()
        _mock_headers["anthropic-ratelimit-unified-status"] = "rejected"
    elif scenario == "approaching-weekly-limit":
        _mock_headers = {
            "anthropic-ratelimit-unified-status": "allowed_warning",
            "anthropic-ratelimit-unified-reset": str(seven_days),
            "anthropic-ratelimit-unified-representative-claim": "seven_day",
        }
    elif scenario == "extra-usage-required":
        _mock_headerless_429_message = "Extra usage is required for long context requests."
    # ... additional scenarios follow same pattern


def get_mock_headers() -> Optional[Dict[str, str]]:
    if not _mock_enabled or os.environ.get("USER_TYPE") != "ant" or not _mock_headers:
        return None
    return dict(_mock_headers)


def get_mock_headerless_429_message() -> Optional[str]:
    if os.environ.get("USER_TYPE") != "ant":
        return None
    env_msg = os.environ.get("CLAUDE_MOCK_HEADERLESS_429")
    if env_msg:
        return env_msg
    if not _mock_enabled:
        return None
    return _mock_headerless_429_message


def get_mock_status() -> str:
    if not _mock_enabled and not _mock_subscription_type:
        return "No mock headers active (using real limits)"
    lines = ["Active mock headers:"]
    for key, value in _mock_headers.items():
        formatted_key = key.replace("anthropic-ratelimit-unified-", "").replace("-", " ").title()
        lines.append(f"  {formatted_key}: {value}")
    return "\n".join(lines)


def clear_mock_headers() -> None:
    global _mock_headers, _exceeded_limits, _mock_subscription_type
    global _mock_fast_mode_rate_limit_duration_ms, _mock_fast_mode_rate_limit_expires_at
    global _mock_headerless_429_message, _mock_enabled
    _mock_headers = {}
    _exceeded_limits = []
    _mock_subscription_type = None
    _mock_fast_mode_rate_limit_duration_ms = None
    _mock_fast_mode_rate_limit_expires_at = None
    _mock_headerless_429_message = None
    _mock_enabled = False


def apply_mock_headers(headers: Dict[str, str]) -> Dict[str, str]:
    mock = get_mock_headers()
    if not mock:
        return headers
    result = dict(headers)
    result.update(mock)
    return result


def should_process_mock_limits() -> bool:
    if os.environ.get("USER_TYPE") != "ant":
        return False
    return _mock_enabled or bool(os.environ.get("CLAUDE_MOCK_HEADERLESS_429"))


def get_scenario_description(scenario: str) -> str:
    descriptions = {
        "normal": "Normal usage, no limits",
        "session-limit-reached": "Session rate limit exceeded",
        "approaching-weekly-limit": "Approaching weekly aggregate limit",
        "weekly-limit-reached": "Weekly aggregate limit exceeded",
        "overage-active": "Using extra usage (overage active)",
        "overage-warning": "Approaching extra usage limit",
        "overage-exhausted": "Both subscription and extra usage limits exhausted",
        "out-of-credits": "Out of extra usage credits (wallet empty)",
        "clear": "Clear mock headers (use real limits)",
    }
    return descriptions.get(scenario, "Unknown scenario")
