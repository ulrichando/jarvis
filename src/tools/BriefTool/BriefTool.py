"""BriefTool (SendUserMessage) -- sends a message to the user."""
from __future__ import annotations
from typing import Any
from src.tools.BriefTool.prompt import BRIEF_TOOL_NAME


async def execute_brief(message: str, **kwargs: Any) -> dict[str, Any]:
    """Send a message to the user. Stub."""
    return {"sent": True, "message": message}
