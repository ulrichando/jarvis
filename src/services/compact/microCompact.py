"""Micro-compaction for individual tool results."""

from __future__ import annotations

from typing import Any, Optional

# Max token size for images in tool results
IMAGE_MAX_TOKEN_SIZE = 2000
# Max tokens for tool result text before truncation
MAX_TOOL_RESULT_TOKENS = 3000


def micro_compact_tool_result(content: str, max_tokens: int = MAX_TOOL_RESULT_TOKENS) -> str:
    """Truncate a tool result to fit within token budget."""
    estimated_tokens = len(content) // 4
    if estimated_tokens <= max_tokens:
        return content

    # Truncate to approximate token limit
    max_chars = max_tokens * 4
    return content[:max_chars] + "\n...[truncated]"


def calculate_tool_result_tokens(block: Any) -> int:
    """Estimate token count for a tool result block."""
    if isinstance(block, str):
        return len(block) // 4
    if isinstance(block, dict):
        block_type = block.get("type", "")
        if block_type in ("image", "document"):
            return IMAGE_MAX_TOKEN_SIZE
        if block_type == "text":
            return len(block.get("text", "")) // 4
    return 0
