"""
Datadog log event tracking.

Batches events and flushes them to Datadog's HTTP intake endpoint.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Set

import aiohttp

from .config import is_analytics_disabled
from .metadata import get_event_metadata


DATADOG_LOGS_ENDPOINT = "https://http-intake.logs.us5.datadoghq.com/api/v2/logs"
DATADOG_CLIENT_TOKEN = "pubbbf48e6d78dae54bceaa4acf463299bf"
DEFAULT_FLUSH_INTERVAL_MS = 15000
MAX_BATCH_SIZE = 100
NETWORK_TIMEOUT_MS = 5000

DATADOG_ALLOWED_EVENTS: Set[str] = {
    "chrome_bridge_connection_succeeded",
    "chrome_bridge_connection_failed",
    "chrome_bridge_disconnected",
    "chrome_bridge_tool_call_completed",
    "chrome_bridge_tool_call_error",
    "chrome_bridge_tool_call_started",
    "chrome_bridge_tool_call_timeout",
    "tengu_api_error",
    "tengu_api_success",
    "tengu_brief_mode_enabled",
    "tengu_brief_mode_toggled",
    "tengu_brief_send",
    "tengu_cancel",
    "tengu_compact_failed",
    "tengu_exit",
    "tengu_flicker",
    "tengu_init",
    "tengu_model_fallback_triggered",
    "tengu_oauth_error",
    "tengu_oauth_success",
    "tengu_oauth_token_refresh_failure",
    "tengu_oauth_token_refresh_success",
    "tengu_oauth_token_refresh_lock_acquiring",
    "tengu_oauth_token_refresh_lock_acquired",
    "tengu_oauth_token_refresh_starting",
    "tengu_oauth_token_refresh_completed",
    "tengu_oauth_token_refresh_lock_releasing",
    "tengu_oauth_token_refresh_lock_released",
    "tengu_query_error",
    "tengu_session_file_read",
    "tengu_started",
    "tengu_tool_use_error",
    "tengu_tool_use_granted_in_prompt_permanent",
    "tengu_tool_use_granted_in_prompt_temporary",
    "tengu_tool_use_rejected_in_prompt",
    "tengu_tool_use_success",
    "tengu_uncaught_exception",
    "tengu_unhandled_rejection",
    "tengu_voice_recording_started",
    "tengu_voice_toggled",
    "tengu_team_mem_sync_pull",
    "tengu_team_mem_sync_push",
    "tengu_team_mem_sync_started",
    "tengu_team_mem_entries_capped",
}

TAG_FIELDS = [
    "arch",
    "client_type",
    "error_type",
    "http_status_range",
    "http_status",
    "kairos_active",
    "model",
    "platform",
    "provider",
    "skill_mode",
    "subscription_type",
    "tool_name",
    "user_bucket",
    "user_type",
    "version",
    "version_base",
]


def _camel_to_snake(name: str) -> str:
    """Convert camelCase to snake_case."""
    return re.sub(r"[A-Z]", lambda m: f"_{m.group(0).lower()}", name)


@dataclass
class DatadogLog:
    ddsource: str
    ddtags: str
    message: str
    service: str
    hostname: str
    extra: dict[str, Any] = field(default_factory=dict)


# Module-level state
_log_batch: list[dict[str, Any]] = []
_flush_task: Optional[asyncio.Task] = None
_datadog_initialized: Optional[bool] = None
_num_user_buckets = 30


async def _flush_logs() -> None:
    """Flush accumulated log batch to Datadog."""
    global _log_batch
    if not _log_batch:
        return

    logs_to_send = _log_batch
    _log_batch = []

    try:
        async with aiohttp.ClientSession() as session:
            await session.post(
                DATADOG_LOGS_ENDPOINT,
                json=logs_to_send,
                headers={
                    "Content-Type": "application/json",
                    "DD-API-KEY": DATADOG_CLIENT_TOKEN,
                },
                timeout=aiohttp.ClientTimeout(total=NETWORK_TIMEOUT_MS / 1000),
            )
    except Exception:
        pass  # Silently fail


def _schedule_flush() -> None:
    """Schedule a flush after the configured interval."""
    global _flush_task
    if _flush_task is not None:
        return

    async def _delayed_flush():
        global _flush_task
        await asyncio.sleep(_get_flush_interval_ms() / 1000)
        _flush_task = None
        await _flush_logs()

    try:
        loop = asyncio.get_event_loop()
        _flush_task = loop.create_task(_delayed_flush())
    except RuntimeError:
        pass


async def initialize_datadog() -> bool:
    """Initialize Datadog logging. Returns True if successful."""
    global _datadog_initialized
    if is_analytics_disabled():
        _datadog_initialized = False
        return False

    try:
        _datadog_initialized = True
        return True
    except Exception:
        _datadog_initialized = False
        return False


async def shutdown_datadog() -> None:
    """Flush remaining logs and shut down."""
    global _flush_task
    if _flush_task is not None:
        _flush_task.cancel()
        _flush_task = None
    await _flush_logs()


async def track_datadog_event(
    event_name: str,
    properties: dict[str, Any],
) -> None:
    """Track an event to Datadog. Only sends in production."""
    global _datadog_initialized

    if os.environ.get("NODE_ENV") != "production":
        return

    initialized = _datadog_initialized
    if initialized is None:
        initialized = await initialize_datadog()
    if not initialized or event_name not in DATADOG_ALLOWED_EVENTS:
        return

    try:
        metadata = await get_event_metadata(model=properties.get("model"))
        all_data: dict[str, Any] = {**metadata, **properties, "user_bucket": _get_user_bucket()}

        # Normalize MCP tool names
        if isinstance(all_data.get("tool_name"), str) and all_data["tool_name"].startswith("mcp__"):
            all_data["tool_name"] = "mcp"

        # Build tags
        tags = [f"event:{event_name}"]
        for f in TAG_FIELDS:
            if all_data.get(f) is not None:
                tags.append(f"{_camel_to_snake(f)}:{all_data[f]}")

        log: dict[str, Any] = {
            "ddsource": "python",
            "ddtags": ",".join(tags),
            "message": event_name,
            "service": "jarvis",
            "hostname": "jarvis",
        }

        for key, value in all_data.items():
            if value is not None:
                log[_camel_to_snake(key)] = value

        _log_batch.append(log)

        if len(_log_batch) >= MAX_BATCH_SIZE:
            global _flush_task
            if _flush_task is not None:
                _flush_task.cancel()
                _flush_task = None
            await _flush_logs()
        else:
            _schedule_flush()
    except Exception:
        pass


def _get_user_bucket() -> int:
    """Hash user ID into a bucket for cardinality reduction."""
    user_id = os.environ.get("USER", "unknown")
    h = hashlib.sha256(user_id.encode()).hexdigest()
    return int(h[:8], 16) % _num_user_buckets


def _get_flush_interval_ms() -> int:
    """Get flush interval, allowing override via environment."""
    try:
        return int(os.environ.get("DATADOG_FLUSH_INTERVAL_MS", ""))
    except (ValueError, TypeError):
        return DEFAULT_FLUSH_INTERVAL_MS
