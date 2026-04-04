"""
Analytics sink implementation.

Routes events to Datadog and 1P event logging.
Call initialize_analytics_sink() during app startup to attach the sink.
"""

from __future__ import annotations

from typing import Any

from .datadog import track_datadog_event
from .firstPartyEventLogger import log_event_to_1p, should_sample_event
from .growthbook import check_statsig_feature_gate_cached_may_be_stale
from .index import LogEventMetadata, attach_analytics_sink, strip_proto_fields
from .sinkKillswitch import is_sink_killed

DATADOG_GATE_NAME = "tengu_log_datadog_events"

# Module-level gate state - starts as None, initialized during startup
_is_datadog_gate_enabled: bool | None = None


def _should_track_datadog() -> bool:
    """
    Check if Datadog tracking is enabled.
    Falls back to cached value from previous session if not yet initialized.
    """
    if is_sink_killed("datadog"):
        return False
    if _is_datadog_gate_enabled is not None:
        return _is_datadog_gate_enabled

    try:
        return check_statsig_feature_gate_cached_may_be_stale(DATADOG_GATE_NAME)
    except Exception:
        return False


def _log_event_impl(event_name: str, metadata: LogEventMetadata) -> None:
    """Log an event (synchronous implementation)."""
    sample_result = should_sample_event(event_name)

    if sample_result == 0:
        return

    metadata_with_sample_rate = (
        {**metadata, "sample_rate": sample_result}
        if sample_result is not None
        else metadata
    )

    if _should_track_datadog():
        track_datadog_event(event_name, strip_proto_fields(metadata_with_sample_rate))

    log_event_to_1p(event_name, metadata_with_sample_rate)


async def _log_event_async_impl(
    event_name: str, metadata: LogEventMetadata
) -> None:
    """Log an event (asynchronous implementation)."""
    _log_event_impl(event_name, metadata)


def initialize_analytics_gates() -> None:
    """
    Initialize analytics gates during startup.
    Updates gate values from server.
    """
    global _is_datadog_gate_enabled
    _is_datadog_gate_enabled = check_statsig_feature_gate_cached_may_be_stale(
        DATADOG_GATE_NAME
    )


class _Sink:
    def log_event(self, event_name: str, metadata: LogEventMetadata) -> None:
        _log_event_impl(event_name, metadata)

    async def log_event_async(
        self, event_name: str, metadata: LogEventMetadata
    ) -> None:
        await _log_event_async_impl(event_name, metadata)


def initialize_analytics_sink() -> None:
    """
    Initialize the analytics sink.
    Call during app startup. Idempotent.
    """
    attach_analytics_sink(_Sink())
