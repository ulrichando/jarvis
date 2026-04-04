"""BashTool result message formatting (no React UI)."""
from __future__ import annotations
from typing import Any


def format_bash_result(
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 0,
    is_error: bool = False,
) -> dict[str, Any]:
    """Format a bash tool result for display."""
    return {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "is_error": is_error,
    }
