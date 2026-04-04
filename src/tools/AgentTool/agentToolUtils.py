"""
Utility functions for the AgentTool.

Includes tool filtering, resolution, result finalization, and async lifecycle management.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ResolvedAgentTools:
    """Result of resolving and validating agent tools against available tools."""
    has_wildcard: bool
    valid_tools: list[str]
    invalid_tools: list[str]
    resolved_tools: list[Any]
    allowed_agent_types: Optional[list[str]] = None


@dataclass
class AgentToolResult:
    """Result from a completed agent tool execution."""
    agent_id: str
    agent_type: Optional[str] = None
    content: list[dict[str, str]] = field(default_factory=list)
    total_tool_use_count: int = 0
    total_duration_ms: int = 0
    total_tokens: int = 0
    usage: Optional[dict[str, Any]] = None


def count_tool_uses(messages: list[dict[str, Any]]) -> int:
    """Count the number of tool_use blocks in assistant messages."""
    count = 0
    for m in messages:
        if m.get("type") == "assistant":
            for block in m.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    count += 1
    return count


def get_last_tool_use_name(message: dict[str, Any]) -> Optional[str]:
    """Returns the name of the last tool_use block in an assistant message."""
    if message.get("type") != "assistant":
        return None
    content = message.get("message", {}).get("content", [])
    for block in reversed(content):
        if block.get("type") == "tool_use":
            return block.get("name")
    return None


def extract_partial_result(messages: list[dict[str, Any]]) -> Optional[str]:
    """Extract a partial result string from an agent's accumulated messages.
    Used when an async agent is killed to preserve what it accomplished.
    """
    for m in reversed(messages):
        if m.get("type") != "assistant":
            continue
        content = m.get("message", {}).get("content", [])
        texts = [block.get("text", "") for block in content if block.get("type") == "text"]
        text = "\n".join(texts).strip()
        if text:
            return text
    return None


def finalize_agent_tool(
    agent_messages: list[dict[str, Any]],
    agent_id: str,
    start_time: float,
    agent_type: str = "",
) -> AgentToolResult:
    """Finalize agent tool execution and produce a result."""
    # Find last assistant message with text content
    content: list[dict[str, str]] = []
    for m in reversed(agent_messages):
        if m.get("type") != "assistant":
            continue
        msg_content = m.get("message", {}).get("content", [])
        text_blocks = [b for b in msg_content if b.get("type") == "text"]
        if text_blocks:
            content = text_blocks
            break

    total_tool_use_count = count_tool_uses(agent_messages)
    duration_ms = int((time.time() - start_time) * 1000)

    return AgentToolResult(
        agent_id=agent_id,
        agent_type=agent_type,
        content=content,
        total_tool_use_count=total_tool_use_count,
        total_duration_ms=duration_ms,
        total_tokens=0,
    )
