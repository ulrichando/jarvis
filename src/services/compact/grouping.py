"""Message grouping for compaction."""

from __future__ import annotations

from typing import Any, List


def group_messages_for_compact(messages: List[Any]) -> List[List[Any]]:
    """Group messages into logical segments for partial compaction.

    Groups by user-assistant exchanges, keeping tool use chains intact.
    """
    groups: List[List[Any]] = []
    current_group: List[Any] = []

    for msg in messages:
        msg_type = msg.get("type", "") if isinstance(msg, dict) else getattr(msg, "type", "")
        if msg_type == "user" and current_group:
            groups.append(current_group)
            current_group = []
        current_group.append(msg)

    if current_group:
        groups.append(current_group)

    return groups
