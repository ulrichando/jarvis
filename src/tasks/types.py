"""Union of all concrete task state types.
Use this for components that need to work with any task type.
"""

from __future__ import annotations

from typing import Union

from .DreamTask.DreamTask import DreamTaskState
from .InProcessTeammateTask.types import InProcessTeammateTaskState
from .LocalAgentTask.LocalAgentTask import LocalAgentTaskState
from .LocalShellTask.guards import LocalShellTaskState
from .RemoteAgentTask.RemoteAgentTask import RemoteAgentTaskState

# Note: LocalWorkflowTaskState and MonitorMcpTaskState not present in this codebase
# Add them when available:
# from .LocalWorkflowTask.LocalWorkflowTask import LocalWorkflowTaskState
# from .MonitorMcpTask.MonitorMcpTask import MonitorMcpTaskState

TaskState = Union[
    LocalShellTaskState,
    LocalAgentTaskState,
    RemoteAgentTaskState,
    InProcessTeammateTaskState,
    DreamTaskState,
]

BackgroundTaskState = Union[
    LocalShellTaskState,
    LocalAgentTaskState,
    RemoteAgentTaskState,
    InProcessTeammateTaskState,
    DreamTaskState,
]


def is_background_task(task: TaskState) -> bool:
    """Check if a task should be shown in the background tasks indicator.
    A task is considered a background task if:
    1. It is running or pending
    2. It has been explicitly backgrounded (not a foreground task)
    """
    if task.get("status") not in ("running", "pending"):
        return False
    # Foreground tasks (is_backgrounded === False) are not yet "background tasks"
    if "is_backgrounded" in task and task["is_backgrounded"] is False:
        return False
    return True
