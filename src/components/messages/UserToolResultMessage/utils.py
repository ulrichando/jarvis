"""Utilities for tool result messages.

Helpers for extracting tool information from message sequences.
"""

from __future__ import annotations
from typing import Any, Optional


def useGetToolFromMessages(
    messages: list[dict[str, Any]],
    tool_use_id: str,
) -> Optional[dict[str, Any]]:
    """Find the tool_use message that corresponds to a tool result.

    Searches backwards through messages to find the tool_use call
    that a tool_result message is responding to.

    Args:
        messages: List of message dicts.
        tool_use_id: The tool_use_id to search for.

    Returns:
        The matching tool_use message dict, or None.
    """
    if not tool_use_id:
        return None

    for msg in reversed(messages):
        # Check if this message contains tool_use blocks
        content = msg.get("content", [])
        if isinstance(content, list):
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("id") == tool_use_id
                ):
                    return block
        # Also check top-level tool calls
        if msg.get("type") == "tool_use" and msg.get("id") == tool_use_id:
            return msg

    return None
