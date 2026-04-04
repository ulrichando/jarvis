"""EnterPlanModeTool -- transitions to plan mode."""
from __future__ import annotations
from typing import Any
from src.tools.EnterPlanModeTool.constants import ENTER_PLAN_MODE_TOOL_NAME


async def execute_enter_plan_mode(**kwargs: Any) -> dict[str, Any]:
    """Enter plan mode. Stub."""
    return {"mode": "plan"}
