"""GrepTool -- content search."""
from __future__ import annotations
from typing import Any
from src.tools.GrepTool.prompt import GREP_TOOL_NAME


async def execute_grep(pattern: str, **kwargs: Any) -> dict[str, Any]:
    """Execute grep search. Actual implementation in brain/agent/tools.py."""
    raise NotImplementedError("Use brain/agent/tools.py for grep search")
