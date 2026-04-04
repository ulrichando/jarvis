"""Bridge poll interval defaults."""

from __future__ import annotations

from dataclasses import dataclass

POLL_INTERVAL_MS_NOT_AT_CAPACITY = 2000
POLL_INTERVAL_MS_AT_CAPACITY = 600_000
MULTISESSION_POLL_INTERVAL_MS_NOT_AT_CAPACITY = POLL_INTERVAL_MS_NOT_AT_CAPACITY
MULTISESSION_POLL_INTERVAL_MS_PARTIAL_CAPACITY = POLL_INTERVAL_MS_NOT_AT_CAPACITY
MULTISESSION_POLL_INTERVAL_MS_AT_CAPACITY = POLL_INTERVAL_MS_AT_CAPACITY


@dataclass
class PollIntervalConfig:
    poll_interval_ms_not_at_capacity: int = POLL_INTERVAL_MS_NOT_AT_CAPACITY
    poll_interval_ms_at_capacity: int = POLL_INTERVAL_MS_AT_CAPACITY
    non_exclusive_heartbeat_interval_ms: int = 0
    multisession_poll_interval_ms_not_at_capacity: int = MULTISESSION_POLL_INTERVAL_MS_NOT_AT_CAPACITY
    multisession_poll_interval_ms_partial_capacity: int = MULTISESSION_POLL_INTERVAL_MS_PARTIAL_CAPACITY
    multisession_poll_interval_ms_at_capacity: int = MULTISESSION_POLL_INTERVAL_MS_AT_CAPACITY
    reclaim_older_than_ms: int = 5000
    session_keepalive_interval_v2_ms: int = 120_000


DEFAULT_POLL_CONFIG = PollIntervalConfig()
