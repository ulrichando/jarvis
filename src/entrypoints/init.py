"""
Initialization entrypoint.

Validates configs, sets up graceful shutdown, telemetry, proxy,
mTLS, and other startup tasks. Memoized so it runs at most once.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from functools import lru_cache
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Track if telemetry has been initialized to prevent double initialization
_telemetry_initialized = False
_init_done = False


async def init() -> None:
    """
    Main initialization function. Memoized -- runs at most once.

    Validates configs, sets up graceful shutdown, initializes telemetry,
    configures proxy/mTLS, and performs other startup tasks.
    """
    global _init_done
    if _init_done:
        return
    _init_done = True

    init_start_time = time.time()
    logger.info("init_started")

    try:
        # Validate and enable configuration system
        configs_start = time.time()
        _enable_configs()
        logger.info(
            "init_configs_enabled duration_ms=%d",
            int((time.time() - configs_start) * 1000),
        )

        # Apply safe environment variables before trust dialog
        env_vars_start = time.time()
        _apply_safe_config_environment_variables()
        _apply_extra_ca_certs_from_config()
        logger.info(
            "init_safe_env_vars_applied duration_ms=%d",
            int((time.time() - env_vars_start) * 1000),
        )

        # Set up graceful shutdown
        _setup_graceful_shutdown()

        # Populate OAuth account info if needed (fire and forget)
        asyncio.ensure_future(_populate_oauth_account_info_if_needed())

        # Detect GitHub repository asynchronously
        asyncio.ensure_future(_detect_current_repository())

        # Record first start time
        _record_first_start_time()

        # Configure global mTLS settings
        mtls_start = time.time()
        logger.debug("[init] configureGlobalMTLS starting")
        _configure_global_mtls()
        logger.info(
            "init_mtls_configured duration_ms=%d",
            int((time.time() - mtls_start) * 1000),
        )

        # Configure global HTTP agents (proxy and/or mTLS)
        proxy_start = time.time()
        logger.debug("[init] configureGlobalAgents starting")
        _configure_global_agents()
        logger.info(
            "init_proxy_configured duration_ms=%d",
            int((time.time() - proxy_start) * 1000),
        )

        # Preconnect to API
        _preconnect_api()

        # Initialize scratchpad directory if enabled
        if _is_scratchpad_enabled():
            scratchpad_start = time.time()
            await _ensure_scratchpad_dir()
            logger.info(
                "init_scratchpad_created duration_ms=%d",
                int((time.time() - scratchpad_start) * 1000),
            )

        logger.info(
            "init_completed duration_ms=%d",
            int((time.time() - init_start_time) * 1000),
        )

    except _ConfigParseError as error:
        logger.error("Configuration error in %s: %s", error.file_path, error)
        raise
    except Exception:
        raise


def initialize_telemetry_after_trust() -> None:
    """
    Initialize telemetry after trust has been granted.

    For remote-settings-eligible users, waits for settings to load,
    then re-applies env vars before initializing telemetry.
    This should only be called once, after the trust dialog has been accepted.
    """
    asyncio.ensure_future(_do_initialize_telemetry())


async def _do_initialize_telemetry() -> None:
    global _telemetry_initialized
    if _telemetry_initialized:
        return

    _telemetry_initialized = True
    try:
        await _set_meter_state()
    except Exception:
        _telemetry_initialized = False
        raise


async def _set_meter_state() -> None:
    """Initialize customer OTLP telemetry (metrics, logs, traces)."""
    # Placeholder -- would lazily load telemetry modules
    logger.debug("Telemetry initialization placeholder")


# ---------------------------------------------------------------------------
# Stub helpers (would be actual implementations or imports)
# ---------------------------------------------------------------------------

class _ConfigParseError(Exception):
    def __init__(self, message: str, file_path: str = ""):
        super().__init__(message)
        self.file_path = file_path


def _enable_configs() -> None:
    """Validate and enable the configuration system."""
    pass


def _apply_safe_config_environment_variables() -> None:
    """Apply only safe environment variables before trust dialog."""
    pass


def _apply_extra_ca_certs_from_config() -> None:
    """Apply NODE_EXTRA_CA_CERTS equivalent from settings."""
    pass


def _setup_graceful_shutdown() -> None:
    """Register signal handlers for graceful shutdown."""
    pass


async def _populate_oauth_account_info_if_needed() -> None:
    pass


async def _detect_current_repository() -> None:
    pass


def _record_first_start_time() -> None:
    pass


def _configure_global_mtls() -> None:
    pass


def _configure_global_agents() -> None:
    pass


def _preconnect_api() -> None:
    pass


def _is_scratchpad_enabled() -> bool:
    return False


async def _ensure_scratchpad_dir() -> None:
    pass
