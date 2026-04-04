"""ExitWorktreeTool -- exits a git worktree."""
from __future__ import annotations
from typing import Any
from src.tools.ExitWorktreeTool.constants import EXIT_WORKTREE_TOOL_NAME


async def execute_exit_worktree(action: str = "keep", **kwargs: Any) -> dict[str, Any]:
    """Exit a worktree. Stub."""
    return {"action": action}
