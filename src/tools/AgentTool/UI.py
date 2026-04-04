"""UI rendering stub. Original was a React component (.tsx).
JARVIS renders tool output via shells/cli/ and shells/web/ instead.
"""
from __future__ import annotations
from typing import Any


def render_tool_result(result: dict[str, Any]) -> str:
    """Render a tool result as text (non-React fallback)."""
    return str(result)
