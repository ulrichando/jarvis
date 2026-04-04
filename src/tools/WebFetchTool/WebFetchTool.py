"""WebFetchTool -- fetches web content."""
from __future__ import annotations
from typing import Any
from src.tools.WebFetchTool.prompt import WEB_FETCH_TOOL_NAME


async def execute_web_fetch(url: str, prompt: str, **kwargs: Any) -> dict[str, Any]:
    """Fetch web content. Actual implementation in brain/agent/tools.py."""
    raise NotImplementedError("Use brain/agent/tools.py for web fetch")
