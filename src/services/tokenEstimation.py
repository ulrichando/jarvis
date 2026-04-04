"""
Token estimation utilities.

Provides rough token count estimation for messages and content,
with file-type aware ratios and API-based counting support.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

TOKEN_COUNT_THINKING_BUDGET = 1024
TOKEN_COUNT_MAX_TOKENS = 2048


def rough_token_count_estimation(content: str, bytes_per_token: int = 4) -> int:
    """Estimate token count from content length."""
    return round(len(content) / bytes_per_token)


def bytes_per_token_for_file_type(file_extension: str) -> int:
    """Returns an estimated bytes-per-token ratio for a file extension.

    Dense JSON has many single-character tokens which makes the
    real ratio closer to 2 rather than the default 4.
    """
    if file_extension in ("json", "jsonl", "jsonc"):
        return 2
    return 4


def rough_token_count_estimation_for_file_type(
    content: str, file_extension: str
) -> int:
    """Like rough_token_count_estimation but uses file-type aware ratio."""
    return rough_token_count_estimation(
        content, bytes_per_token_for_file_type(file_extension)
    )


def rough_token_count_estimation_for_content(
    content: Optional[Union[str, List[Any]]],
) -> int:
    """Estimate tokens for message content (string or content blocks)."""
    if not content:
        return 0
    if isinstance(content, str):
        return rough_token_count_estimation(content)

    total = 0
    for block in content:
        total += _rough_token_count_for_block(block)
    return total


def _rough_token_count_for_block(block: Any) -> int:
    """Estimate tokens for a single content block."""
    if isinstance(block, str):
        return rough_token_count_estimation(block)
    if not isinstance(block, dict):
        return 0

    block_type = block.get("type", "")

    if block_type == "text":
        return rough_token_count_estimation(block.get("text", ""))
    elif block_type in ("image", "document"):
        # Conservative estimate matching API behavior
        return 2000
    elif block_type == "tool_result":
        return rough_token_count_estimation_for_content(block.get("content"))
    elif block_type == "tool_use":
        name = block.get("name", "")
        input_data = block.get("input", {})
        return rough_token_count_estimation(name + json.dumps(input_data, default=str))
    elif block_type == "thinking":
        return rough_token_count_estimation(block.get("thinking", ""))
    elif block_type == "redacted_thinking":
        return rough_token_count_estimation(block.get("data", ""))
    else:
        return rough_token_count_estimation(json.dumps(block, default=str))


def rough_token_count_estimation_for_messages(
    messages: List[Dict[str, Any]],
) -> int:
    """Estimate total tokens across multiple messages."""
    total = 0
    for msg in messages:
        total += rough_token_count_estimation_for_message(msg)
    return total


def rough_token_count_estimation_for_message(message: Dict[str, Any]) -> int:
    """Estimate tokens for a single message."""
    msg_type = message.get("type", "")

    if msg_type in ("assistant", "user"):
        content = message.get("message", {}).get("content")
        return rough_token_count_estimation_for_content(content)

    if msg_type == "attachment" and message.get("attachment"):
        # Simplified - in practice would normalize attachment for API
        return 0

    return 0


async def count_tokens_with_api(content: str) -> Optional[int]:
    """Count tokens using the API. Returns None on failure."""
    if not content:
        return 0
    # Placeholder - would call actual API
    return rough_token_count_estimation(content)


async def count_messages_tokens_with_api(
    messages: List[Any], tools: List[Any]
) -> Optional[int]:
    """Count tokens for messages and tools using the API."""
    # Placeholder - would call actual API
    return None
