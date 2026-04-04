"""SendMessageTool -- sends messages between agents."""
from __future__ import annotations
from typing import Any
from src.tools.SendMessageTool.constants import SEND_MESSAGE_TOOL_NAME


async def execute_send_message(to: str, message: str, **kwargs: Any) -> dict[str, Any]:
    """Send a message to another agent. Stub."""
    return {"to": to, "sent": True}
