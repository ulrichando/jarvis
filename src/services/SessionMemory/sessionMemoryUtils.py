"""
Session Memory utility functions.

Separated from main sessionMemory.py to avoid circular dependencies.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

EXTRACTION_WAIT_TIMEOUT_S = 15.0
EXTRACTION_STALE_THRESHOLD_S = 60.0


@dataclass
class SessionMemoryConfig:
    """Configuration for session memory extraction thresholds."""
    minimum_message_tokens_to_init: int = 10000
    minimum_tokens_between_update: int = 5000
    tool_calls_between_updates: int = 3


DEFAULT_SESSION_MEMORY_CONFIG = SessionMemoryConfig()

# Module-level state
_session_memory_config = SessionMemoryConfig()
_last_summarized_message_id: Optional[str] = None
_extraction_started_at: Optional[float] = None
_tokens_at_last_extraction: int = 0
_session_memory_initialized: bool = False


def get_last_summarized_message_id() -> Optional[str]:
    return _last_summarized_message_id


def set_last_summarized_message_id(message_id: Optional[str]) -> None:
    global _last_summarized_message_id
    _last_summarized_message_id = message_id


def mark_extraction_started() -> None:
    global _extraction_started_at
    _extraction_started_at = time.time()


def mark_extraction_completed() -> None:
    global _extraction_started_at
    _extraction_started_at = None


async def wait_for_session_memory_extraction() -> None:
    """Wait for any in-progress extraction to complete (with timeout)."""
    start = time.time()
    while _extraction_started_at is not None:
        age = time.time() - _extraction_started_at
        if age > EXTRACTION_STALE_THRESHOLD_S:
            return
        if time.time() - start > EXTRACTION_WAIT_TIMEOUT_S:
            return
        await asyncio.sleep(1.0)


async def get_session_memory_content() -> Optional[str]:
    """Get the current session memory content from disk."""
    memory_dir = os.environ.get("JARVIS_HOME", os.path.expanduser("~/.jarvis"))
    memory_path = Path(memory_dir) / "session_memory.md"

    try:
        if memory_path.exists():
            return memory_path.read_text()
        return None
    except Exception:
        return None


def set_session_memory_config(config: dict) -> None:
    """Update session memory configuration."""
    global _session_memory_config
    for key, value in config.items():
        if hasattr(_session_memory_config, key):
            setattr(_session_memory_config, key, value)


def get_session_memory_config() -> SessionMemoryConfig:
    return SessionMemoryConfig(**vars(_session_memory_config))


def record_extraction_token_count(current_token_count: int) -> None:
    global _tokens_at_last_extraction
    _tokens_at_last_extraction = current_token_count


def is_session_memory_initialized() -> bool:
    return _session_memory_initialized


def mark_session_memory_initialized() -> None:
    global _session_memory_initialized
    _session_memory_initialized = True


def has_met_initialization_threshold(current_token_count: int) -> bool:
    return current_token_count >= _session_memory_config.minimum_message_tokens_to_init


def has_met_update_threshold(current_token_count: int) -> bool:
    tokens_since = current_token_count - _tokens_at_last_extraction
    return tokens_since >= _session_memory_config.minimum_tokens_between_update


def get_tool_calls_between_updates() -> int:
    return _session_memory_config.tool_calls_between_updates


def reset_session_memory_state() -> None:
    """Reset all session memory state (useful for testing)."""
    global _session_memory_config, _tokens_at_last_extraction
    global _session_memory_initialized, _last_summarized_message_id
    global _extraction_started_at
    _session_memory_config = SessionMemoryConfig()
    _tokens_at_last_extraction = 0
    _session_memory_initialized = False
    _last_summarized_message_id = None
    _extraction_started_at = None
