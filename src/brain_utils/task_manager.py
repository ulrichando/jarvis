"""Task management utilities for JARVIS.

Provides a lightweight task list with support for hierarchical subtasks,
status tracking, serialization, and CLI-friendly formatting.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional


TaskStatus = Literal["pending", "in_progress", "done", "blocked"]

_STATUS_ICONS = {
    "pending": "[ ]",
    "in_progress": "[~]",
    "done": "[x]",
    "blocked": "[!]",
}


@dataclass
class Task:
    """A single task entry with optional subtask hierarchy."""

    id: str
    title: str
    status: TaskStatus = "pending"
    created_at: str = ""
    updated_at: str = ""
    parent_id: Optional[str] = None
    subtasks: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        now = _now_iso()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _short_id() -> str:
    """Generate a short unique task ID."""
    return uuid.uuid4().hex[:8]


class TaskList:
    """Ordered collection of tasks with hierarchy support."""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._order: list[str] = []  # top-level insertion order

    # -- CRUD -----------------------------------------------------------------

    def add(self, title: str, parent_id: str | None = None) -> Task:
        """Add a new task, optionally as a subtask of *parent_id*.

        Returns the created Task.
        Raises KeyError if parent_id does not exist.
        """
        task_id = _short_id()
        task = Task(id=task_id, title=title, parent_id=parent_id)
        self._tasks[task_id] = task

        if parent_id is not None:
            parent = self._get_or_raise(parent_id)
            parent.subtasks.append(task_id)
            parent.updated_at = _now_iso()
        else:
            self._order.append(task_id)

        return task

    def update(
        self,
        task_id: str,
        status: TaskStatus | None = None,
        title: str | None = None,
    ) -> None:
        """Update task status and/or title.

        Raises KeyError if task_id does not exist.
        """
        task = self._get_or_raise(task_id)
        if status is not None:
            task.status = status
        if title is not None:
            task.title = title
        task.updated_at = _now_iso()

    def remove(self, task_id: str) -> None:
        """Remove a task and all of its subtasks recursively.

        Raises KeyError if task_id does not exist.
        """
        task = self._get_or_raise(task_id)

        # Remove subtasks recursively (copy list to avoid mutation during iter)
        for sub_id in list(task.subtasks):
            if sub_id in self._tasks:
                self.remove(sub_id)

        # Unlink from parent
        if task.parent_id and task.parent_id in self._tasks:
            parent = self._tasks[task.parent_id]
            if task_id in parent.subtasks:
                parent.subtasks.remove(task_id)

        # Remove from top-level order
        if task_id in self._order:
            self._order.remove(task_id)

        del self._tasks[task_id]

    def get(self, task_id: str) -> Task:
        """Return a task by ID. Raises KeyError if not found."""
        return self._get_or_raise(task_id)

    # -- Queries --------------------------------------------------------------

    def list(self, status_filter: TaskStatus | None = None) -> list[Task]:
        """Return all tasks (flat), optionally filtered by status."""
        tasks = list(self._tasks.values())
        if status_filter is not None:
            tasks = [t for t in tasks if t.status == status_filter]
        return tasks

    def progress(self) -> tuple[int, int]:
        """Return (done_count, total_count) across all tasks."""
        total = len(self._tasks)
        done = sum(1 for t in self._tasks.values() if t.status == "done")
        return done, total

    # -- Display --------------------------------------------------------------

    def format(self) -> str:
        """Render the task list with checkbox icons for CLI display."""
        if not self._tasks:
            return "No tasks."

        done, total = self.progress()
        lines: list[str] = [f"Tasks ({done}/{total} done)", ""]

        for task_id in self._order:
            self._format_task(task_id, lines, indent=0)

        return "\n".join(lines).rstrip()

    def _format_task(
        self, task_id: str, lines: list[str], indent: int
    ) -> None:
        task = self._tasks.get(task_id)
        if task is None:
            return
        icon = _STATUS_ICONS.get(task.status, "[ ]")
        prefix = "  " * indent
        lines.append(f"{prefix}{icon} {task.title}")
        for sub_id in task.subtasks:
            self._format_task(sub_id, lines, indent + 1)

    # -- Serialization --------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialize the task list to a plain dict."""
        return {
            "tasks": {
                tid: {
                    "id": t.id,
                    "title": t.title,
                    "status": t.status,
                    "created_at": t.created_at,
                    "updated_at": t.updated_at,
                    "parent_id": t.parent_id,
                    "subtasks": list(t.subtasks),
                }
                for tid, t in self._tasks.items()
            },
            "order": list(self._order),
        }

    @classmethod
    def from_dict(cls, data: dict) -> TaskList:
        """Deserialize a TaskList from a plain dict."""
        tl = cls()
        for tid, td in data.get("tasks", {}).items():
            task = Task(
                id=td["id"],
                title=td["title"],
                status=td.get("status", "pending"),
                created_at=td.get("created_at", ""),
                updated_at=td.get("updated_at", ""),
                parent_id=td.get("parent_id"),
                subtasks=list(td.get("subtasks", [])),
            )
            tl._tasks[tid] = task
        tl._order = list(data.get("order", []))
        return tl

    # -- Internals ------------------------------------------------------------

    def _get_or_raise(self, task_id: str) -> Task:
        try:
            return self._tasks[task_id]
        except KeyError:
            raise KeyError(f"Task '{task_id}' not found") from None
