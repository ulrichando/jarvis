"""
Unary event logging for completions (accept/reject/response).
"""

from dataclasses import dataclass
from typing import Literal, Optional
import logging

logger = logging.getLogger(__name__)

CompletionType = Literal[
    "str_replace_single",
    "str_replace_multi",
    "write_file_single",
    "tool_use_single",
]


@dataclass
class LogEventMetadata:
    language_name: str
    message_id: str
    platform: str
    has_feedback: Optional[bool] = None


@dataclass
class LogEvent:
    completion_type: CompletionType
    event: Literal["accept", "reject", "response"]
    metadata: LogEventMetadata


async def log_unary_event(event: LogEvent) -> None:
    """Log a unary event for analytics."""
    logger.debug(
        "unary_event: event=%s completion_type=%s language=%s message_id=%s platform=%s",
        event.event,
        event.completion_type,
        event.metadata.language_name,
        event.metadata.message_id,
        event.metadata.platform,
    )
