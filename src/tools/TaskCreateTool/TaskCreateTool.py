"""TaskCreateTool -- creates tasks."""
from __future__ import annotations
from typing import Any
from src.tools.TaskCreateTool.constants import TASK_CREATE_TOOL_NAME


async def execute_task_create(subject: str, description: str = "", **kwargs: Any) -> dict[str, Any]:
    """Create a task. Stub."""
    return {"task_id": "", "subject": subject}
