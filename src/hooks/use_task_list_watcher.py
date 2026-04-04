"""Task list directory watcher for tasks mode."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

DEBOUNCE_MS = 1.0  # seconds


@dataclass
class Task:
    id: str
    subject: str
    description: Optional[str] = None
    status: str = "pending"
    owner: Optional[str] = None
    blocked_by: List[str] = None

    def __post_init__(self):
        if self.blocked_by is None:
            self.blocked_by = []


def find_available_task(tasks: List[Task]) -> Optional[Task]:
    """Find an available task that can be worked on.

    Available = status 'pending', no owner, not blocked by unresolved tasks.
    """
    unresolved_ids = {t.id for t in tasks if t.status != "completed"}

    for task in tasks:
        if task.status != "pending":
            continue
        if task.owner:
            continue
        if all(bid not in unresolved_ids for bid in task.blocked_by):
            return task
    return None


def format_task_as_prompt(task: Task) -> str:
    """Format a task as a prompt for the AI to work on."""
    prompt = f"Complete all open tasks. Start with task #{task.id}: \n\n {task.subject}"
    if task.description:
        prompt += f"\n\n{task.description}"
    return prompt


class TaskListWatcher:
    """Watches a task list directory and automatically picks up open tasks.

    Enables 'tasks mode' where the system watches for externally-created
    tasks and processes them one at a time.

    Equivalent to useTaskListWatcher React hook.
    """

    def __init__(
        self,
        task_list_id: Optional[str] = None,
        on_submit_task: Optional[Callable[[str], bool]] = None,
        list_tasks: Optional[Callable] = None,
        claim_task: Optional[Callable] = None,
        update_task: Optional[Callable] = None,
    ):
        self.task_list_id = task_list_id
        self._on_submit_task = on_submit_task
        self._list_tasks = list_tasks
        self._claim_task = claim_task
        self._update_task = update_task
        self._current_task_id: Optional[str] = None
        self._is_loading = False
        self._running = False

    @property
    def enabled(self) -> bool:
        return self.task_list_id is not None

    def set_is_loading(self, loading: bool) -> None:
        self._is_loading = loading

    async def check_for_tasks(self) -> None:
        """Check for available tasks and submit one if found."""
        if not self.enabled or self._is_loading:
            return

        if not self._list_tasks:
            return

        tasks = await self._list_tasks(self.task_list_id)

        # Check if current task is resolved
        if self._current_task_id is not None:
            current = next((t for t in tasks if t.id == self._current_task_id), None)
            if not current or current.status == "completed":
                self._current_task_id = None
            else:
                return  # Still working

        available = find_available_task(tasks)
        if not available:
            return

        # Claim the task
        if self._claim_task:
            result = await self._claim_task(
                self.task_list_id, available.id, self.task_list_id
            )
            if not result.get("success"):
                return

        self._current_task_id = available.id
        prompt = format_task_as_prompt(available)

        if self._on_submit_task:
            submitted = self._on_submit_task(prompt)
            if not submitted and self._update_task:
                await self._update_task(
                    self.task_list_id, available.id, {"owner": None}
                )
                self._current_task_id = None

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False
