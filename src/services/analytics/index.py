"""
Analytics service - public API for event logging.

This module serves as the main entry point for analytics events.

DESIGN: This module has NO dependencies to avoid import cycles.
Events are queued until attach_analytics_sink() is called during app initialization.
The sink handles routing to backends.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol


# Marker types -- in Python these are just documentation
AnalyticsMetadata_I_VERIFIED_THIS_IS_NOT_CODE_OR_FILEPATHS = str
AnalyticsMetadata_I_VERIFIED_THIS_IS_PII_TAGGED = str

LogEventMetadata = dict[str, bool | int | float | None]


def strip_proto_fields(metadata: dict[str, Any]) -> dict[str, Any]:
    """
    Strip _PROTO_* keys from a payload destined for general-access storage.

    Returns the input unchanged (same reference) when no _PROTO_ keys present.
    """
    has_proto = any(k.startswith("_PROTO_") for k in metadata)
    if not has_proto:
        return metadata
    return {k: v for k, v in metadata.items() if not k.startswith("_PROTO_")}


@dataclass
class QueuedEvent:
    event_name: str
    metadata: LogEventMetadata
    is_async: bool


class AnalyticsSink(Protocol):
    def log_event(self, event_name: str, metadata: LogEventMetadata) -> None: ...
    async def log_event_async(
        self, event_name: str, metadata: LogEventMetadata
    ) -> None: ...


# Event queue for events logged before sink is attached
_event_queue: list[QueuedEvent] = []

# Sink - initialized during app startup
_sink: Optional[AnalyticsSink] = None


def attach_analytics_sink(new_sink: AnalyticsSink) -> None:
    """
    Attach the analytics sink that will receive all events.
    Queued events are drained asynchronously.

    Idempotent: if a sink is already attached, this is a no-op.
    """
    global _sink
    if _sink is not None:
        return
    _sink = new_sink

    if _event_queue:
        queued_events = list(_event_queue)
        _event_queue.clear()

        for event in queued_events:
            if event.is_async:
                asyncio.ensure_future(
                    _sink.log_event_async(event.event_name, event.metadata)
                )
            else:
                _sink.log_event(event.event_name, event.metadata)


def log_event(event_name: str, metadata: LogEventMetadata | None = None) -> None:
    """
    Log an event to analytics backends (synchronous).

    If no sink is attached, events are queued and drained when the sink attaches.
    """
    if metadata is None:
        metadata = {}
    if _sink is None:
        _event_queue.append(
            QueuedEvent(event_name=event_name, metadata=metadata, is_async=False)
        )
        return
    _sink.log_event(event_name, metadata)


async def log_event_async(
    event_name: str, metadata: LogEventMetadata | None = None
) -> None:
    """
    Log an event to analytics backends (asynchronous).

    If no sink is attached, events are queued and drained when the sink attaches.
    """
    if metadata is None:
        metadata = {}
    if _sink is None:
        _event_queue.append(
            QueuedEvent(event_name=event_name, metadata=metadata, is_async=True)
        )
        return
    await _sink.log_event_async(event_name, metadata)


def _reset_for_testing() -> None:
    """Reset analytics state for testing purposes only."""
    global _sink
    _sink = None
    _event_queue.clear()
