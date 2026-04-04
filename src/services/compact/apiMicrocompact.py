"""API-level micro-compaction for reducing request sizes."""

from __future__ import annotations

from typing import Any, List


def api_micro_compact_messages(messages: List[Any], budget: int) -> List[Any]:
    """Apply micro-compaction to messages before sending to API."""
    from .microCompact import micro_compact_tool_result

    compacted = []
    for msg in messages:
        if isinstance(msg, dict) and msg.get("type") == "tool_result":
            content = msg.get("content", "")
            if isinstance(content, str):
                msg = {**msg, "content": micro_compact_tool_result(content)}
        compacted.append(msg)
    return compacted
