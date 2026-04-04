"""
Token counting and usage utilities for messages.
"""

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: Optional[int] = None
    cache_read_input_tokens: Optional[int] = None


@dataclass
class Message:
    type: str  # "user", "assistant", "system", etc.
    message: Dict[str, Any] = None

    def __post_init__(self):
        if self.message is None:
            self.message = {}


SYNTHETIC_MESSAGES = set()
SYNTHETIC_MODEL = "__synthetic__"


def get_token_usage(message: Message) -> Optional[Usage]:
    """Get token usage from a message if it's a real assistant response."""
    if message.type != "assistant":
        return None

    msg = message.message
    if not msg:
        return None

    usage_data = msg.get("usage")
    if not usage_data:
        return None

    content = msg.get("content", [])
    if (
        isinstance(content, list)
        and len(content) > 0
        and isinstance(content[0], dict)
        and content[0].get("type") == "text"
        and content[0].get("text") in SYNTHETIC_MESSAGES
    ):
        return None

    if msg.get("model") == SYNTHETIC_MODEL:
        return None

    if isinstance(usage_data, Usage):
        return usage_data

    return Usage(
        input_tokens=usage_data.get("input_tokens", 0),
        output_tokens=usage_data.get("output_tokens", 0),
        cache_creation_input_tokens=usage_data.get("cache_creation_input_tokens"),
        cache_read_input_tokens=usage_data.get("cache_read_input_tokens"),
    )


def get_token_count_from_usage(usage: Usage) -> int:
    """
    Calculate total context window tokens from usage data.
    Includes input_tokens + cache tokens + output_tokens.
    """
    return (
        usage.input_tokens
        + (usage.cache_creation_input_tokens or 0)
        + (usage.cache_read_input_tokens or 0)
        + usage.output_tokens
    )


def token_count_from_last_api_response(messages: List[Message]) -> int:
    """Get token count from the last API response in the message list."""
    for i in range(len(messages) - 1, -1, -1):
        usage = get_token_usage(messages[i])
        if usage:
            return get_token_count_from_usage(usage)
    return 0


def message_token_count_from_last_api_response(messages: List[Message]) -> int:
    """Get only the output_tokens from the last API response."""
    for i in range(len(messages) - 1, -1, -1):
        usage = get_token_usage(messages[i])
        if usage:
            return usage.output_tokens
    return 0


def get_current_usage(messages: List[Message]) -> Optional[Dict[str, int]]:
    """Get current usage from the most recent assistant message."""
    for i in range(len(messages) - 1, -1, -1):
        usage = get_token_usage(messages[i])
        if usage:
            return {
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cache_creation_input_tokens": usage.cache_creation_input_tokens or 0,
                "cache_read_input_tokens": usage.cache_read_input_tokens or 0,
            }
    return None


def does_most_recent_assistant_message_exceed_200k(messages: List[Message]) -> bool:
    """Check if the most recent assistant message exceeds 200k tokens."""
    threshold = 200_000
    for msg in reversed(messages):
        if msg.type == "assistant":
            usage = get_token_usage(msg)
            if usage:
                return get_token_count_from_usage(usage) > threshold
            return False
    return False


def get_assistant_message_content_length(message: Message) -> int:
    """
    Calculate the character content length of an assistant message.
    Used for spinner token estimation (characters / 4 ~ tokens).
    """
    content_length = 0
    content = message.message.get("content", [])
    if not isinstance(content, list):
        return 0

    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            content_length += len(block.get("text", ""))
        elif block_type == "thinking":
            content_length += len(block.get("thinking", ""))
        elif block_type == "redacted_thinking":
            content_length += len(block.get("data", ""))
        elif block_type == "tool_use":
            content_length += len(json.dumps(block.get("input", {})))

    return content_length


# Rough token estimation: ~4 chars per token
CHARS_PER_TOKEN = 4


def rough_token_count_estimation(messages: Sequence[Message]) -> int:
    """Rough token count from messages based on character length."""
    total_chars = 0
    for msg in messages:
        content = msg.message.get("content", "")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        total_chars += len(block.get("text", ""))
                    elif block.get("type") == "tool_result":
                        result_content = block.get("content", "")
                        if isinstance(result_content, str):
                            total_chars += len(result_content)
    return total_chars // CHARS_PER_TOKEN


def token_count_with_estimation(messages: Sequence[Message]) -> int:
    """
    Get the current context window size in tokens.

    Uses the last API response's token count plus estimates for any
    messages added since.
    """
    for i in range(len(messages) - 1, -1, -1):
        usage = get_token_usage(messages[i])
        if usage:
            return (
                get_token_count_from_usage(usage)
                + rough_token_count_estimation(messages[i + 1:])
            )
    return rough_token_count_estimation(messages)
