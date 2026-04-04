"""Rate limit warning notification."""

from __future__ import annotations

from typing import Callable, Optional


def check_rate_limit_warning(
    add_notification: Optional[Callable] = None,
    rate_limit_remaining: Optional[int] = None,
    rate_limit_reset: Optional[int] = None,
) -> None:
    """Show warning when approaching rate limits.

    Equivalent to useRateLimitWarningNotification React hook.
    """
    if not add_notification or rate_limit_remaining is None:
        return
    if rate_limit_remaining <= 5:
        add_notification(
            key="rate-limit-warning",
            text=f"Rate limit: {rate_limit_remaining} requests remaining",
            priority="high",
        )
