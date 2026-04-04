"""BashTool -- executes bash commands."""
from __future__ import annotations
from typing import Any
from src.tools.BashTool.toolName import BASH_TOOL_NAME


async def execute_bash(command: str, **kwargs: Any) -> dict[str, Any]:
    """Execute a bash command. Actual implementation in brain/agent/tools.py."""
    raise NotImplementedError("Use brain/agent/tools.py for bash execution")
