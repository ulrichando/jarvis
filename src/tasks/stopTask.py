"""Shared logic for stopping a running task.
Used by TaskStopTool (LLM-invoked) and SDK stop_task control request.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional


class StopTaskError(Exception):
    """Error raised when a task cannot be stopped."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code  # 'not_found' | 'not_running' | 'unsupported_type'


@dataclass
class StopTaskResult:
    task_id: str
    task_type: str
    command: Optional[str]


@dataclass
class StopTaskContext:
    get_app_state: Callable[[], Any]
    set_app_state: Callable[[Callable], None]


def is_local_shell_task(task: Any) -> bool:
    """Check if a task is a local shell task."""
    return (
        isinstance(task, dict)
        and task.get("type") == "local_bash"
    )


async def stop_task(task_id: str, context: StopTaskContext) -> StopTaskResult:
    """Look up a task by ID, validate it is running, kill it, and mark it as notified.

    Raises StopTaskError when the task cannot be stopped (not found,
    not running, or unsupported type). Callers can inspect error.code to
    distinguish the failure reason.
    """
    app_state = context.get_app_state()
    tasks = app_state.get("tasks", {})
    task = tasks.get(task_id)

    if task is None:
        raise StopTaskError(f"No task found with ID: {task_id}", "not_found")

    if task.get("status") != "running":
        raise StopTaskError(
            f"Task {task_id} is not running (status: {task.get('status')})",
            "not_running",
        )

    task_type = task.get("type")
    # Task implementation lookup would go here
    # task_impl = get_task_by_type(task_type)
    # if not task_impl:
    #     raise StopTaskError(f"Unsupported task type: {task_type}", "unsupported_type")
    # await task_impl.kill(task_id, context.set_app_state)

    # Bash: suppress the "exit code 137" notification (noise). Agent tasks: don't
    # suppress -- the AbortError catch sends a notification carrying
    # extract_partial_result(agent_messages), which is the payload not noise.
    if is_local_shell_task(task):
        suppressed = False

        def update_for_shell(prev):
            nonlocal suppressed
            prev_task = prev.get("tasks", {}).get(task_id)
            if prev_task is None or prev_task.get("notified"):
                return prev
            suppressed = True
            new_tasks = {**prev["tasks"], task_id: {**prev_task, "notified": True}}
            return {**prev, "tasks": new_tasks}

        context.set_app_state(update_for_shell)

    command = task.get("command") if is_local_shell_task(task) else task.get("description")

    return StopTaskResult(task_id=task_id, task_type=task_type, command=command)
