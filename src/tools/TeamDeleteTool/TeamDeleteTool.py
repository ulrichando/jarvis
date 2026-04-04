"""TeamDeleteTool -- deletes a team."""
from __future__ import annotations
from typing import Any
from src.tools.TeamDeleteTool.constants import TEAM_DELETE_TOOL_NAME


async def execute_team_delete(**kwargs: Any) -> dict[str, Any]:
    """Delete a team. Stub."""
    return {"deleted": True}
