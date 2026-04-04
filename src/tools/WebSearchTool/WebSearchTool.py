"""WebSearchTool -- searches the web."""
from __future__ import annotations
from typing import Any
from src.tools.WebSearchTool.prompt import WEB_SEARCH_TOOL_NAME


async def execute_web_search(query: str, **kwargs: Any) -> dict[str, Any]:
    """Search the web. Actual implementation in brain/agent/tools.py."""
    raise NotImplementedError("Use brain/agent/tools.py for web search")
