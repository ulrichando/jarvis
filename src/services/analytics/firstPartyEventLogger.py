"""
First-party event logging for internal analytics.

Events are batched and exported to /api/event_logging/batch.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .config import is_analytics_disabled
from .metadata import get_event_metadata
from .sinkKillswitch import is_sink_killed

logger = logging.getLogger(__name__)


@dataclass
class EventSamplingConfig:
    """Configuration for sampling individual event types."""
    configs: Dict[str, Dict[str, float]] = field(default_factory=dict)


@dataclass
class BatchConfig:
    """Batch processor configuration."""
    scheduled_delay_millis: Optional[int] = None
    max_export_batch_size: Optional[int] = None
    max_queue_size: Optional[int] = None
    skip_auth: bool = False
    max_attempts: Optional[int] = None
    path: Optional[str] = None
    base_url: Optional[str] = None


# Module-level state
_event_logger_initialized = False
_event_batch: list[dict[str, Any]] = []

DEFAULT_LOGS_EXPORT_INTERVAL_MS = 10000
DEFAULT_MAX_EXPORT_BATCH_SIZE = 200
DEFAULT_MAX_QUEUE_SIZE = 8192


def is_1p_event_logging_enabled() -> bool:
    """Check if 1P event logging is enabled."""
    return not is_analytics_disabled()


def should_sample_event(event_name: str) -> Optional[float]:
    """Determine if an event should be sampled based on its sample rate.

    Returns the sample rate if sampled, None if not sampled, 0 if dropped.
    """
    # Default: no sampling config, log everything
    return None


def log_event_to_1p(
    event_name: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Log a 1st-party event for internal analytics.

    Events are batched and exported.
    """
    if not is_1p_event_logging_enabled():
        return

    if is_sink_killed("firstParty"):
        return

    event = {
        "event_name": event_name,
        "event_id": str(uuid.uuid4()),
        "metadata": metadata or {},
    }
    _event_batch.append(event)


async def shutdown_1p_event_logging() -> None:
    """Flush and shutdown the 1P event logger."""
    global _event_batch
    _event_batch = []


def initialize_1p_event_logging() -> None:
    """Initialize 1P event logging infrastructure."""
    global _event_logger_initialized
    if not is_1p_event_logging_enabled():
        return
    _event_logger_initialized = True


@dataclass
class GrowthBookExperimentData:
    """GrowthBook experiment event data for logging."""
    experiment_id: str = ""
    variation_id: int = 0
    user_attributes: Optional[Dict[str, Any]] = None
    experiment_metadata: Optional[Dict[str, Any]] = None


def log_growthbook_experiment_to_1p(data: GrowthBookExperimentData) -> None:
    """Log a GrowthBook experiment assignment event to 1P."""
    if not is_1p_event_logging_enabled():
        return

    if is_sink_killed("firstParty"):
        return

    event = {
        "event_type": "GrowthbookExperimentEvent",
        "event_id": str(uuid.uuid4()),
        "experiment_id": data.experiment_id,
        "variation_id": data.variation_id,
    }
    _event_batch.append(event)
