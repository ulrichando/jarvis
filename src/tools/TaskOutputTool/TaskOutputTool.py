"""TaskOutputTool -- gets task output."""
from __future__ import annotations
from typing import Any
from src.tools.TaskOutputTool.constants import TASK_OUTPUT_TOOL_NAME


async def execute_task_output(task_id: str, **kwargs: Any) -> dict[str, Any]:
    """Get task output. Stub."""
    return {"task_id": task_id, "output": ""}
