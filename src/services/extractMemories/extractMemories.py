"""
Memory extraction from session transcripts.

Extracts durable memories from the current session and writes them
to the auto-memory directory. Runs at the end of each complete
query loop via stop hooks.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from .prompts import build_extract_auto_only_prompt

logger = logging.getLogger(__name__)

# Tools allowed for the extraction agent
ALLOWED_TOOLS = {"Read", "Grep", "Glob", "Bash", "Edit", "Write"}
# Bash commands allowed (read-only)
ALLOWED_BASH_COMMANDS = {"ls", "find", "cat", "stat", "wc", "head", "tail", "grep"}


def is_model_visible_message(message: Any) -> bool:
    """Returns True if a message is visible to the model."""
    msg_type = message.get("type", "") if isinstance(message, dict) else getattr(message, "type", "")
    return msg_type in ("user", "assistant")


def create_auto_mem_can_use_tool(memory_path: str) -> Callable:
    """Create a tool permission function that allows memory-related tools only."""
    async def can_use_tool(tool_name: str, tool_input: Any) -> Dict[str, Any]:
        if tool_name not in ALLOWED_TOOLS:
            return {
                "behavior": "deny",
                "message": f"Tool {tool_name} is not allowed for memory extraction",
            }

        # For write tools, only allow writes to the memory directory
        if tool_name in ("Edit", "Write"):
            file_path = ""
            if isinstance(tool_input, dict):
                file_path = tool_input.get("file_path", "") or tool_input.get("path", "")
            if not file_path.startswith(memory_path):
                return {
                    "behavior": "deny",
                    "message": "Writes are only allowed to the memory directory",
                }

        # For bash, only allow read-only commands
        if tool_name == "Bash":
            command = ""
            if isinstance(tool_input, dict):
                command = tool_input.get("command", "")
            first_cmd = command.split()[0] if command.split() else ""
            base_cmd = first_cmd.rsplit("/", 1)[-1] if "/" in first_cmd else first_cmd
            if base_cmd not in ALLOWED_BASH_COMMANDS:
                return {
                    "behavior": "deny",
                    "message": f"Bash command '{base_cmd}' is not allowed for memory extraction",
                }

        return {"behavior": "allow", "message": ""}

    return can_use_tool


class MemoryExtractor:
    """Manages memory extraction from conversation sessions."""

    def __init__(self) -> None:
        self._last_extracted_message_id: Optional[str] = None
        self._running = False

    async def maybe_extract(
        self,
        messages: List[Any],
        memory_path: str,
    ) -> bool:
        """Extract memories if conditions are met.

        Returns True if extraction was performed.
        """
        if self._running:
            return False

        model_messages = [m for m in messages if is_model_visible_message(m)]
        if len(model_messages) < 4:
            return False

        self._running = True
        try:
            await self._run_extraction(model_messages, memory_path)
            return True
        except Exception as e:
            logger.error(f"[extractMemories] Failed: {e}")
            return False
        finally:
            self._running = False

    async def _run_extraction(
        self,
        messages: List[Any],
        memory_path: str,
    ) -> None:
        """Run the actual memory extraction."""
        new_count = len(messages)
        prompt = build_extract_auto_only_prompt(new_count, "")
        logger.debug(f"[extractMemories] Would extract from {new_count} messages")
        # In a full implementation, this would fork the conversation
        # and run the extraction prompt through an LLM


def init_extract_memories() -> MemoryExtractor:
    """Initialize the memory extractor."""
    return MemoryExtractor()
