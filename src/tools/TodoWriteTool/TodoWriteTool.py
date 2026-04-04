"""TodoWriteTool -- manages todo lists."""
from __future__ import annotations
from typing import Any
from src.tools.TodoWriteTool.constants import TODO_WRITE_TOOL_NAME


async def execute_todo_write(todos: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    """Write/update the todo list. Stub."""
    return {"todos": todos}
