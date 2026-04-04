"""Tool use summary generator for conversation context."""

from __future__ import annotations

from typing import Any, Dict, List


def generate_tool_use_summary(tool_calls: List[Dict[str, Any]]) -> str:
    """Generate a human-readable summary of tool usage.

    Used to provide context about what tools were used and their results.
    """
    if not tool_calls:
        return "No tools were used."

    lines = []
    for call in tool_calls:
        name = call.get("name", "unknown")
        is_error = call.get("is_error", False)
        status = "failed" if is_error else "succeeded"
        lines.append(f"- {name}: {status}")

    return f"Tool usage summary ({len(tool_calls)} calls):\n" + "\n".join(lines)
