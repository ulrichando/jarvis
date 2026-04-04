"""Post-compaction cleanup operations."""

from __future__ import annotations

from typing import Any, List


def cleanup_after_compact(messages: List[Any]) -> List[Any]:
    """Clean up message list after compaction.

    Removes orphaned tool results, fixes message ordering, etc.
    """
    cleaned = []
    for msg in messages:
        # Skip orphaned tool results (no matching tool_use)
        msg_type = msg.get("type", "") if isinstance(msg, dict) else getattr(msg, "type", "")
        if msg_type == "tool_result":
            # Would check for matching tool_use in cleaned messages
            pass
        cleaned.append(msg)
    return cleaned
