"""
Tags user messages with a sourceToolUseID so they stay transient until the tool resolves.
Extracts tool use IDs from parent messages.
"""

from __future__ import annotations

from typing import Any, Optional


def tag_messages_with_tool_use_id(
    messages: list[dict[str, Any]],
    tool_use_id: Optional[str],
) -> list[dict[str, Any]]:
    """
    Tags user messages with a sourceToolUseID so they stay transient until the tool resolves.
    This prevents the "is running" message from being duplicated in the UI.
    """
    if not tool_use_id:
        return messages
    result = []
    for m in messages:
        if m.get("type") == "user":
            result.append({**m, "sourceToolUseID": tool_use_id})
        else:
            result.append(m)
    return result


def get_tool_use_id_from_parent_message(
    parent_message: dict[str, Any],
    tool_name: str,
) -> Optional[str]:
    """
    Extracts the tool use ID from a parent message for a given tool name.
    """
    content = parent_message.get("message", {}).get("content", [])
    for block in content:
        if block.get("type") == "tool_use" and block.get("name") == tool_name:
            return block.get("id")
    return None
