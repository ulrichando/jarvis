"""
Session memory management.

Manages the extraction and updating of session memory - a persistent
summary of the current conversation that helps maintain context.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from .sessionMemoryUtils import (
    get_session_memory_config,
    has_met_initialization_threshold,
    has_met_update_threshold,
    is_session_memory_initialized,
    mark_extraction_completed,
    mark_extraction_started,
    mark_session_memory_initialized,
    record_extraction_token_count,
)

logger = logging.getLogger(__name__)


class SessionMemoryManager:
    """Manages session memory extraction and updates."""

    def __init__(self) -> None:
        self._tool_calls_since_last_update = 0
        self._running = False

    async def on_tool_call(self, messages: List[Any], current_tokens: int) -> None:
        """Called after each tool call to potentially trigger memory update."""
        self._tool_calls_since_last_update += 1

        config = get_session_memory_config()

        if not is_session_memory_initialized():
            if has_met_initialization_threshold(current_tokens):
                mark_session_memory_initialized()
                await self._extract(messages, current_tokens)
            return

        if (
            self._tool_calls_since_last_update >= config.tool_calls_between_updates
            and has_met_update_threshold(current_tokens)
        ):
            await self._extract(messages, current_tokens)

    async def _extract(self, messages: List[Any], current_tokens: int) -> None:
        """Run session memory extraction."""
        if self._running:
            return

        self._running = True
        mark_extraction_started()
        try:
            self._tool_calls_since_last_update = 0
            record_extraction_token_count(current_tokens)
            # In a full implementation, this would fork the conversation
            # and extract session memory via LLM
            logger.debug("[sessionMemory] Would extract session memory")
        except Exception as e:
            logger.error(f"[sessionMemory] Extraction failed: {e}")
        finally:
            self._running = False
            mark_extraction_completed()


def init_session_memory() -> SessionMemoryManager:
    """Initialize session memory management."""
    return SessionMemoryManager()
