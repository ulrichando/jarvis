"""TaskGetTool -- gets a task by ID."""
from __future__ import annotations
from typing import Any
from src.tools.TaskGetTool.constants import TASK_GET_TOOL_NAME


async def execute_task_get(task_id: str, **kwargs: Any) -> dict[str, Any]:
    """Get a task by ID. Stub."""
    return {"task_id": task_id}
