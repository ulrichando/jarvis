"""
Utility for inserting a block into a content array relative to tool_result blocks.
"""

from __future__ import annotations

from typing import Any


def insert_block_after_tool_results(content: list[Any], block: Any) -> None:
    """
    Inserts a block into the content array after the last tool_result block.
    Mutates the list in place.

    Placement rules:
    - If tool_result blocks exist: insert after the last one
    - Otherwise: insert before the last block
    - If the inserted block would be the final element, a text continuation
      block is appended
    """
    last_tool_result_index = -1
    for i, item in enumerate(content):
        if (
            isinstance(item, dict)
            and item.get("type") == "tool_result"
        ):
            last_tool_result_index = i

    if last_tool_result_index >= 0:
        insert_pos = last_tool_result_index + 1
        content.insert(insert_pos, block)
        # Append a text continuation if the inserted block is now last
        if insert_pos == len(content) - 1:
            content.append({"type": "text", "text": "."})
    else:
        # No tool_result blocks -- insert before the last block
        insert_index = max(0, len(content) - 1)
        content.insert(insert_index, block)
