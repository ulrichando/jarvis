"""Tool rendering for computer use (non-JSX logic)."""

from __future__ import annotations


def format_computer_use_result(action_type: str, result: str) -> str:
    """Format a computer use tool result for display."""
    return f"[computer-use:{action_type}] {result}"
