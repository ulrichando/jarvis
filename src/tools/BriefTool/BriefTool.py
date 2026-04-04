"""BriefTool (SendUserMessage) -- sends a message to the user."""
from __future__ import annotations
from typing import Any
from src.tools.BriefTool.prompt import BRIEF_TOOL_NAME


def is_brief_entitled() -> bool:
    """Check whether the current session is entitled to use brief mode.

    Returns True when kairos is active or the user has opted in.
    """
    from src.bootstrap.state import get_kairos_active, get_user_msg_opt_in
    return get_kairos_active() or get_user_msg_opt_in()


async def execute_brief(message: str, **kwargs: Any) -> dict[str, Any]:
    """Send a message to the user. Stub."""
    return {"sent": True, "message": message}
