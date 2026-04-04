"""Context analysis utilities for token budget management."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class TokenStats:
    tool_requests: dict[str, int] = field(default_factory=dict)
    tool_results: dict[str, int] = field(default_factory=dict)
    human_messages: int = 0
    assistant_messages: int = 0
    local_command_outputs: int = 0
    other: int = 0
    attachments: dict[str, int] = field(default_factory=dict)
    duplicate_file_reads: dict[str, dict[str, int]] = field(default_factory=dict)
    total: int = 0


@dataclass
class ContextData:
    percentage: int = 0
    raw_max_tokens: int = 200_000
    is_auto_compact_enabled: bool = True
    message_breakdown: Optional[Any] = None


def rough_token_count(text: str) -> int:
    """Rough estimate of token count (~4 chars per token)."""
    return len(text) // 4


def analyze_context(messages: list[dict[str, Any]]) -> TokenStats:
    """Analyze context window usage by message type."""
    stats = TokenStats()

    for msg in messages:
        msg_type = msg.get("type", "")
        content = msg.get("message", {}).get("content", "")

        if isinstance(content, str):
            tokens = rough_token_count(content)
            stats.total += tokens
            if msg_type == "user":
                if "local-command-stdout" in content:
                    stats.local_command_outputs += tokens
                else:
                    stats.human_messages += tokens
            elif msg_type == "assistant":
                stats.assistant_messages += tokens
            else:
                stats.other += tokens
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    block_type = block.get("type", "")
                    if block_type == "tool_use":
                        name = block.get("name", "unknown")
                        tokens = rough_token_count(str(block.get("input", "")))
                        stats.tool_requests[name] = stats.tool_requests.get(name, 0) + tokens
                        stats.total += tokens
                    elif block_type == "tool_result":
                        tokens = rough_token_count(str(block.get("content", "")))
                        stats.tool_results["result"] = stats.tool_results.get("result", 0) + tokens
                        stats.total += tokens
                    elif block_type == "text":
                        text = block.get("text", "")
                        tokens = rough_token_count(text)
                        stats.total += tokens
                        if msg_type == "user":
                            stats.human_messages += tokens
                        else:
                            stats.assistant_messages += tokens

    return stats
