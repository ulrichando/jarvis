"""
Daily token budget guard.
Call budget_guard.add(tokens) after every Claude API call.
Call budget_guard.reset() at midnight (wire to a cron job).
"""

import logging

logger = logging.getLogger(__name__)

# Adjust to your comfort level
DAILY_TOKEN_BUDGET: int = 500_000

# Fire the first alert at this fraction of the daily budget
ALERT_THRESHOLD: float = 0.80


class BudgetGuard:
    """
    Tracks cumulative token spend for the current day.
    Logs a CRITICAL warning when spend crosses ALERT_THRESHOLD.
    is_over_budget() returns True once the full budget is exhausted —
    callers should route to Qwen instead of Claude at that point.
    """

    def __init__(self) -> None:
        self._total:   int  = 0
        self._alerted: bool = False

    def add(self, tokens: int) -> None:
        """Record token spend and fire the alert if the threshold is crossed."""
        self._total += tokens

        if not self._alerted and self._total >= DAILY_TOKEN_BUDGET * ALERT_THRESHOLD:
            logger.critical(
                f"[BUDGET] WARNING: {self._total:,} tokens used today — "
                f"{(self._total / DAILY_TOKEN_BUDGET) * 100:.1f}% of daily budget "
                f"({DAILY_TOKEN_BUDGET:,} token limit)"
            )
            self._alerted = True

    def is_over_budget(self) -> bool:
        """True once the daily budget is fully exhausted."""
        return self._total > DAILY_TOKEN_BUDGET

    @property
    def total_tokens(self) -> int:
        return self._total

    @property
    def percent_used(self) -> float:
        return (self._total / DAILY_TOKEN_BUDGET) * 100

    def reset(self) -> None:
        """Reset counters — call this at midnight via a cron task."""
        logger.info(
            f"[BUDGET] Daily reset — used {self._total:,} tokens "
            f"({self.percent_used:.1f}% of budget)"
        )
        self._total   = 0
        self._alerted = False


# Global singleton — import this in models.py
budget_guard = BudgetGuard()
