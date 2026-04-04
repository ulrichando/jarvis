"""TaskListTool -- lists all tasks."""
from __future__ import annotations
from typing import Any
from src.tools.TaskListTool.constants import TASK_LIST_TOOL_NAME


async def execute_task_list(**kwargs: Any) -> dict[str, Any]:
    """List all tasks. Stub."""
    return {"tasks": []}
