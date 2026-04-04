"""Task registry - get all tasks and find tasks by type."""

from __future__ import annotations

from typing import List, Optional

from .Task import Task, TaskType


def get_all_tasks() -> List[Task]:
    """Get all registered tasks.

    In the TypeScript version, this loads tasks from various task modules.
    This Python version provides a simplified registry.
    """
    # Tasks would be registered here in a full implementation
    return []


def get_task_by_type(task_type: TaskType) -> Optional[Task]:
    """Get a task by its type."""
    for task in get_all_tasks():
        if task.type == task_type:
            return task
    return None
