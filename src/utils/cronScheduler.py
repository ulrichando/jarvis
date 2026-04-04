"""Non-blocking cron scheduler for scheduled tasks."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .cronJitterConfig import CronJitterConfig, DEFAULT_CRON_JITTER_CONFIG

logger = logging.getLogger(__name__)

CHECK_INTERVAL_MS = 1000


def is_recurring_task_aged(
    task: dict[str, Any], now_ms: int, max_age_ms: int
) -> bool:
    """Check if a recurring task has exceeded its max age."""
    if max_age_ms == 0:
        return False
    return bool(
        task.get("recurring")
        and not task.get("permanent")
        and now_ms - task.get("createdAt", 0) >= max_age_ms
    )


@dataclass
class CronScheduler:
    on_fire: Callable[[str], None]
    is_loading: Callable[[], bool]
    _running: bool = False

    def start(self) -> None:
        """Start the scheduler."""
        self._running = True
        logger.debug("Cron scheduler started")

    def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        logger.debug("Cron scheduler stopped")


def create_cron_scheduler(
    on_fire: Callable[[str], None],
    is_loading: Callable[[], bool],
) -> CronScheduler:
    """Create a new cron scheduler."""
    return CronScheduler(on_fire=on_fire, is_loading=is_loading)
