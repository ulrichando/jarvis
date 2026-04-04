"""Cron jitter configuration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CronJitterConfig:
    recurring_frac: float = 0.1
    recurring_cap_ms: int = 300_000
    one_shot_max_ms: int = 300_000
    one_shot_floor_ms: int = 0
    one_shot_minute_mod: int = 5
    recurring_max_age_ms: int = 30 * 24 * 60 * 60 * 1000  # 30 days


DEFAULT_CRON_JITTER_CONFIG = CronJitterConfig()


def get_cron_jitter_config() -> CronJitterConfig:
    """Get the cron jitter configuration."""
    return DEFAULT_CRON_JITTER_CONFIG
