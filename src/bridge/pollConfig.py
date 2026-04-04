"""Bridge poll interval config with GrowthBook refresh."""

from __future__ import annotations

from .pollConfigDefaults import DEFAULT_POLL_CONFIG, PollIntervalConfig


def get_poll_interval_config() -> PollIntervalConfig:
    """Fetch the bridge poll interval config. Falls back to defaults."""
    return DEFAULT_POLL_CONFIG
