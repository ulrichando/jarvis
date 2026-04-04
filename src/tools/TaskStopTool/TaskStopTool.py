"""TaskStopTool -- stops a running task."""
from __future__ import annotations
from typing import Any
from src.tools.TaskStopTool.prompt import TASK_STOP_TOOL_NAME


async def execute_task_stop(task_id: str, **kwargs: Any) -> dict[str, Any]:
    """Stop a task. Stub."""
    return {"task_id": task_id, "stopped": True}
