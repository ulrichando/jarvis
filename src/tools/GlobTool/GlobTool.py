"""GlobTool -- file pattern matching."""
from __future__ import annotations
from typing import Any
from src.tools.GlobTool.prompt import GLOB_TOOL_NAME


async def execute_glob(pattern: str, **kwargs: Any) -> dict[str, Any]:
    """Execute glob search. Actual implementation in brain/agent/tools.py."""
    raise NotImplementedError("Use brain/agent/tools.py for glob search")
