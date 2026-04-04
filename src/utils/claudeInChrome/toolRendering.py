"""Tool rendering for Chrome browser automation (non-JSX logic)."""

from __future__ import annotations


def format_tool_result(tool_name: str, result: str) -> str:
    """Format a Chrome tool result for display."""
    return f"[{tool_name}] {result}"
