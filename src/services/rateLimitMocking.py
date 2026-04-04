"""
Facade for rate limit header processing.

Isolates mock logic from production code.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .mockRateLimits import (
    apply_mock_headers,
    get_mock_headerless_429_message,
    get_mock_headers,
    should_process_mock_limits,
)


def process_rate_limit_headers(headers: Dict[str, str]) -> Dict[str, str]:
    """Process headers, applying mocks if /mock-limits command is active."""
    if should_process_mock_limits():
        return apply_mock_headers(headers)
    return headers


def should_process_rate_limits(is_subscriber: bool) -> bool:
    """Check if we should process rate limits."""
    return is_subscriber or should_process_mock_limits()


def check_mock_rate_limit_error(
    current_model: str,
    is_fast_mode_active: bool = False,
) -> Optional[Exception]:
    """Check if mock rate limits should throw a 429 error.

    Returns the error to throw, or None if no error should be thrown.
    """
    if not should_process_mock_limits():
        return None

    headerless_message = get_mock_headerless_429_message()
    if headerless_message:
        return Exception(f"429: {headerless_message}")

    mock_headers = get_mock_headers()
    if not mock_headers:
        return None

    status = mock_headers.get("anthropic-ratelimit-unified-status")
    overage_status = mock_headers.get("anthropic-ratelimit-unified-overage-status")
    rate_limit_type = mock_headers.get("anthropic-ratelimit-unified-representative-claim")

    # Opus-specific limits only fire when using Opus
    if rate_limit_type == "seven_day_opus" and "opus" not in current_model:
        return None

    should_throw_429 = status == "rejected" and (
        not overage_status or overage_status == "rejected"
    )

    if should_throw_429:
        return Exception("429: Rate limit exceeded")

    return None


def is_mock_rate_limit_error(error: Exception) -> bool:
    """Check if this is a mock 429 error that shouldn't be retried."""
    return should_process_mock_limits() and "429" in str(error)
