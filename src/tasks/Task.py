"""Task type definitions and utilities."""

from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

TaskType = Literal[
    "local_bash",
    "local_agent",
    "remote_agent",
    "in_process_teammate",
    "local_workflow",
    "monitor_mcp",
    "dream",
]

TaskStatus = Literal["pending", "running", "completed", "failed", "killed"]


def is_terminal_task_status(status: TaskStatus) -> bool:
    """True when a task is in a terminal state and will not transition further."""
    return status in ("completed", "failed", "killed")


@dataclass
class TaskHandle:
    task_id: str
    cleanup: Optional[Callable[[], None]] = None


SetAppState = Callable  # Simplified type


@dataclass
class TaskContext:
    abort_controller: Any  # AbortController equivalent
    get_app_state: Callable
    set_app_state: SetAppState


@dataclass
class TaskStateBase:
    """Base fields shared by all task states."""
    id: str
    type: TaskType
    status: TaskStatus
    description: str
    tool_use_id: Optional[str] = None
    start_time: float = 0.0
    end_time: Optional[float] = None
    total_paused_ms: Optional[float] = None
    output_file: str = ""
    output_offset: int = 0
    notified: bool = False


@dataclass
class LocalShellSpawnInput:
    command: str
    description: str
    timeout: Optional[int] = None
    tool_use_id: Optional[str] = None
    agent_id: Optional[str] = None
    kind: Optional[Literal["bash", "monitor"]] = None


@dataclass
class Task:
    """Task definition for dispatch."""
    name: str
    type: TaskType

    async def kill(self, task_id: str, set_app_state: SetAppState) -> None:
        """Kill a running task."""
        raise NotImplementedError


# Task ID prefixes
TASK_ID_PREFIXES = {
    "local_bash": "b",
    "local_agent": "a",
    "remote_agent": "r",
    "in_process_teammate": "t",
    "local_workflow": "w",
    "monitor_mcp": "m",
    "dream": "d",
}

# Case-insensitive-safe alphabet
TASK_ID_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def _get_task_id_prefix(task_type: TaskType) -> str:
    return TASK_ID_PREFIXES.get(task_type, "x")


def generate_task_id(task_type: TaskType) -> str:
    """Generate a unique task ID with type prefix."""
    prefix = _get_task_id_prefix(task_type)
    random_bytes = secrets.token_bytes(8)
    suffix = ""
    for b in random_bytes:
        suffix += TASK_ID_ALPHABET[b % len(TASK_ID_ALPHABET)]
    return prefix + suffix


def _get_task_output_path(task_id: str) -> str:
    """Get the output file path for a task."""
    config_home = os.environ.get(
        "JARVIS_HOME", os.path.expanduser("~/.jarvis")
    )
    return os.path.join(config_home, "tasks", f"{task_id}.output")


def create_task_state_base(
    id: str,
    task_type: TaskType,
    description: str,
    tool_use_id: Optional[str] = None,
) -> TaskStateBase:
    """Create a base task state."""
    return TaskStateBase(
        id=id,
        type=task_type,
        status="pending",
        description=description,
        tool_use_id=tool_use_id,
        start_time=time.time() * 1000,
        output_file=_get_task_output_path(id),
        output_offset=0,
        notified=False,
    )
