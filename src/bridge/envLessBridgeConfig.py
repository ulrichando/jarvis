"""Env-less bridge configuration defaults."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EnvLessBridgeConfig:
    init_retry_max_attempts: int = 3
    init_retry_base_delay_ms: int = 500
    init_retry_jitter_fraction: float = 0.25
    init_retry_max_delay_ms: int = 4000
    http_timeout_ms: int = 10_000
    uuid_dedup_buffer_size: int = 2000
    heartbeat_interval_ms: int = 20_000
    heartbeat_jitter_fraction: float = 0.1
    token_refresh_buffer_ms: int = 5 * 60 * 1000
    teardown_archive_timeout_ms: int = 2000
    connect_timeout_ms: int = 15_000
    min_version: str = "0.0.0"
    should_show_app_upgrade_message: bool = False


DEFAULT_ENV_LESS_BRIDGE_CONFIG = EnvLessBridgeConfig()


def get_env_less_bridge_config() -> EnvLessBridgeConfig:
    """Get the env-less bridge config, with GrowthBook overrides."""
    return DEFAULT_ENV_LESS_BRIDGE_CONFIG


def check_env_less_bridge_min_version() -> str | None:
    """Check if CLI version meets minimum for env-less bridge."""
    return None
