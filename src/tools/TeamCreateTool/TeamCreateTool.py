"""TeamCreateTool -- creates a team."""
from __future__ import annotations
from typing import Any
from src.tools.TeamCreateTool.constants import TEAM_CREATE_TOOL_NAME


async def execute_team_create(team_name: str, description: str = "", **kwargs: Any) -> dict[str, Any]:
    """Create a team. Stub."""
    return {"team_name": team_name}
