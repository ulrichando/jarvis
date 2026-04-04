"""Type guards for local shell tasks."""

from __future__ import annotations

from typing import Any


def is_local_shell_task(task: Any) -> bool:
    """Check if a task is a local shell task."""
    return hasattr(task, "shell") and hasattr(task, "pid")


def is_task_running(task: Any) -> bool:
    """Check if a task is currently running."""
    return getattr(task, "status", None) == "running"

class LocalShellTaskState:
    PENDING = "pending"
    RUNNING = "running"  
    COMPLETED = "completed"
    FAILED = "failed"
    KILLED = "killed"

